# Runbook — `orders-api` remediation rollout

## 0. Pre-deployment validation (before touching production)

1. Lint/dry-run the manifests:
   ```
   kubectl apply --dry-run=server -f solution/deployment-and-service.yaml -n production
   kubeconform -strict solution/deployment-and-service.yaml   # or `kubeval`, schema check
   ```
2. Confirm the Secret exists and has the expected key (do **not** print the value):
   ```
   kubectl -n production get secret orders-api-db-credentials -o jsonpath='{.data}' | jq 'keys'
   # expect: ["database-url"]
   ```
3. Run the local validator (`tests/validate.py` — see observability.md/README) against the manifest to catch port mismatches, plaintext secrets, and missing probes before it ever reaches the cluster:
   ```
   python3 tests/validate.py solution/deployment-and-service.yaml
   ```
4. Confirm `/live` and `/ready` actually exist and behave as expected on the 1.9.0 image, outside of the cluster if possible (e.g. run the image locally / in a scratch namespace) — this validates the assumption in `incident_analysis.md` before it's baked into the manifest:
   ```
   docker run -p 8080:8080 registry.example.com/orders-api:1.9.0
   curl -i localhost:8080/live
   curl -i localhost:8080/ready
   ```
5. Check nothing else in the namespace hardcodes the old Service port:
   ```
   grep -R "8081" k8s/production/ 2>/dev/null
   ```

## 1. Rollout sequence

1. Apply the Service and PodDisruptionBudget first (safe, no pod impact):
   ```
   kubectl apply -f solution/deployment-and-service.yaml -n production --dry-run=server
   kubectl apply -f solution/deployment-and-service.yaml -n production
   ```
   (Applying the whole file is fine — Service/PDB changes don't restart pods, and the Deployment change triggers a *controlled* rolling update because of `maxSurge:1 / maxUnavailable:0`.)
2. Watch the rollout live rather than assuming success:
   ```
   kubectl -n production rollout status deploy/orders-api --timeout=5m
   ```
3. While it progresses, watch pods and events in a second terminal:
   ```
   kubectl -n production get pods -l app=orders-api -w
   kubectl -n production get events --sort-by=.lastTimestamp -w
   ```

## 2. Health and service checks after deployment

- All pods Ready and on the new image:
  ```
  kubectl -n production get pods -l app=orders-api -o wide
  kubectl -n production get deploy orders-api -o jsonpath='{.status.availableReplicas}/{.status.replicas}'
  ```
- Service endpoints point at the new pods on the correct port:
  ```
  kubectl -n production get endpoints orders-api -o yaml
  ```
- Direct probe checks against a live pod (bypassing the Service, to isolate app health from routing):
  ```
  kubectl -n production exec deploy/orders-api -- curl -s -o /dev/null -w '%{http_code}\n' localhost:8080/live
  kubectl -n production exec deploy/orders-api -- curl -s -o /dev/null -w '%{http_code}\n' localhost:8080/ready
  ```
- End-to-end through the Service/ingress (from wherever the ingress normally receives traffic):
  ```
  curl -i https://<ingress-host>/orders/health-check-route
  ```
- Resource headroom, not just "not OOMKilled yet":
  ```
  kubectl -n production top pod -l app=orders-api
  ```
- Metrics scrape is working:
  ```
  kubectl -n production exec deploy/orders-api -- curl -s localhost:8080/metrics | head
  ```

## 3. Success signals

- `rollout status` reports "successfully rolled out" within the timeout.
- 3/3 pods `Ready`, all running `orders-api:1.9.0`, `RESTARTS` stays at 0 for at least a 10-minute soak.
- `Endpoints` for the Service list 3 pod IPs on port matching the container's `http` port.
- 5xx rate at ingress returns to baseline (compare against the last 24h p50/p99 for this route).
- Memory stays meaningfully below the 320Mi limit under normal load (target: comfortably under ~70% of limit).

## 4. Failure signals / rollback trigger

Roll back immediately if, within the 10-minute post-rollout soak window, **any** of:

- `kubectl -n production rollout status` does not complete within the timeout, or any new pod enters `CrashLoopBackOff`.
- Ingress 5xx rate for `orders-api` exceeds baseline by a defined threshold (e.g. >2x the pre-deploy 5-minute rolling average) for more than 2 consecutive minutes.
- Any pod is `OOMKilled` again.
- Readiness/liveness failure counts are non-zero and increasing across more than one pod.
- Error budget burn for the 99.95% SLO would exceed the fast-burn alert threshold (see observability.md) if the current error rate continued.

## 5. Rollback procedure

1. Immediate, safe rollback to the last known-good ReplicaSet:
   ```
   kubectl -n production rollout undo deploy/orders-api
   kubectl -n production rollout status deploy/orders-api --timeout=5m
   ```
2. If the rollback itself stalls (e.g. the *Service* change is the actual problem and reverting the Deployment alone won't fix it), separately revert the Service to its prior `targetPort` only if that was confirmed correct for the currently-running image:
   ```
   kubectl -n production patch svc orders-api -p '{"spec":{"ports":[{"name":"http","port":80,"targetPort":8081}]}}'
   ```
   (Only do this if rolling back the Deployment restores 1.8.2 pods that actually listen on 8081 — confirm with `kubectl exec ... printenv` / logs first, don't guess.)
3. Confirm recovery using the same checks as Section 2.
4. Do **not** delete all pods and do **not** disable/bypass readiness or liveness probes as a way to "force" pods healthy — that hides the failure from the Service and from Kubernetes' own self-healing instead of fixing it, and directly violates the availability constraint for this incident.
5. Once stable, open a follow-up to fix forward (apply the corrected manifest again) during a lower-risk window, informed by whatever the rollback investigation found.
6. Record: what changed, when, who approved, and the validation evidence in the incident timeline/postmortem.
