# Incident Analysis — `orders-api` degraded after 1.8.2 → 1.9.0 rollout

## Symptoms (what we observe, not root causes)

- Intermittent `502` responses to customers.
- Some new pods stuck `0/1 Running` (never Ready); one new pod in `CrashLoopBackOff`.
- Ingress logs: `upstream connect error ... upstream=10.42.4.18:8081 error="connection refused"`.
- Pod terminated `Reason: OOMKilled`, `Exit Code: 137`.
- Readiness probe returns `404`; liveness probe returns `503`.
- Old ReplicaSet pod (`orders-api-6c8bcb6d77-vr6tx`, image 1.8.2) is still `1/1 Running` and healthy.

These are all effects. Below are the underlying faults that produce them.

## Root Cause 1 — Service routes to the wrong port (traffic routing fault)

**Evidence:**
- Service `targetPort: 8081`.
- Deployment `containerPort: 8080`, and app log confirms `listening on 0.0.0.0:8080`.
- Ingress error is a TCP-level `connection refused` to port `8081` on the new pod IP — not an HTTP error — which means nothing is listening on 8081 at all.
- The old pod (1.8.2) is `1/1 Ready` and serving traffic fine under the same Service, implying the old image *was* listening on 8081, and 1.9.0 changed its bind port to 8080 without the Service/manifest being updated to match.

**Conclusion:** The image change (1.8.2 → 1.9.0) altered the application's listening port from 8081 to 8080, but `Service.spec.ports[0].targetPort` was left at `8081`. New pods are unreachable through the Service regardless of their health, producing the customer-facing 502s. This is the primary cause of the 502s and would persist even if every other issue were fixed.

## Root Cause 2 — Readiness/liveness probes do not match the documented application contract

**Evidence:**
- App contract: `GET /live` (process liveness), `GET /ready` (dependency-aware readiness).
- Manifest has it backwards and also uses a nonexistent path:
  - `readinessProbe` → `path: /health` → app log shows `GET /health 404` (endpoint doesn't exist).
  - `livenessProbe` → `path: /ready` → this endpoint is explicitly *dependency-aware* per the app contract, and logs show it returning `503` for several seconds while `dependency=postgres state=warming_up`, then `200` once Postgres is ready.
- `livenessProbe.initialDelaySeconds: 5`, `periodSeconds: 5` — with default `failureThreshold: 3`, the container can be killed for liveness failure at ~20s in, which is inside the observed Postgres warm-up window.

**Conclusion:** Two independent probe faults:
1. Readiness will *never* succeed (hits a 404 path), so pods can never become Ready, which is enough by itself to explain "Running 0/1" pods.
2. Liveness is wired to a dependency-aware endpoint, so a slow/unready dependency (Postgres warming up) causes Kubernetes to kill and restart an otherwise-healthy process. This is a classic liveness-vs-readiness inversion and is the direct cause of `CrashLoopBackOff`.

## Root Cause 3 — Memory limit undersized for the new image

**Evidence:**
- `resources.limits.memory: 128Mi`.
- App log: `memory allocation failed rss_mb=127` — right at the ceiling.
- `kubectl top pod`: the *old*, stable 1.8.2 pod is already using `176Mi` — over the new 128Mi limit — and the new pods are at `118–126Mi` and climbing.
- Pod terminated with `OOMKilled` / exit code `137`.

**Conclusion:** 1.9.0's real working set (and likely 1.8.2's, judging by the running pod) exceeds the configured memory limit. The limit was not adjusted for actual observed usage, so the kernel OOM-kills the container under normal operation, independent of the routing/probe issues.

## Root Cause 4 — Rollout strategy has zero surge capacity

**Evidence:**
- `strategy.rollingUpdate: maxSurge: 0, maxUnavailable: 1` with only `replicas: 3`.

**Conclusion:** With `maxSurge: 0`, Kubernetes must terminate an existing healthy pod *before* starting a replacement — it can never run above 3 pods during the rollout. Combined with Root Causes 1–3 (new pods never become Ready), this guarantees a period where only 1–2 pods are actually serving traffic against a 99.95% availability target. This isn't why the rollout *failed*, but it's why the failure was allowed to impact customer-facing availability instead of being contained to spare capacity.

## Root Cause 5 — Plaintext database credential in the manifest

**Evidence:**
- `DATABASE_URL: "postgres://orders_user:plain-text-password@orders-db:5432/orders"` is a literal value in the Deployment spec.

**Conclusion:** Not the cause of this specific outage, but a standing operational/security fault: credentials in plain env values are visible via `kubectl get deploy -o yaml`, in version control, and in any dump of the manifest, and can't be rotated without a redeploy. Should be sourced from a Secret.

## First checks to run on a real cluster (in order)

1. `kubectl -n production get deploy orders-api -o yaml` and `kubectl -n production get rs -l app=orders-api` — confirm which ReplicaSet is current and compare pod template port config against the running image.
2. `kubectl -n production get endpoints orders-api -o yaml` — confirm which pod IPs/ports the Service is actually routing to right now.
3. `kubectl -n production logs <new-pod> --previous` — get the log from before the last restart to confirm the OOM/probe failure sequence.
4. `kubectl -n production describe pod <new-pod>` — re-check probe failure history and OOM event details already captured in Evidence C.
5. `kubectl -n production exec <old-1.8.2-pod> -- printenv PORT` (or equivalent) and check the 1.9.0 image's Dockerfile/`EXPOSE`/startup config or release notes for the actual listen port, to confirm the 8081→8080 port change instead of assuming it from logs alone.
6. Check Postgres itself (`orders-db`) for connection latency / cold-start behavior around deploy time, to confirm "warming_up" is a normal transient state and not a symptom of a separate DB-side issue.

## Assumptions / uncertainties to verify before changing production

- **Assumed:** 1.9.0 intentionally moved the listen port to 8080 (a legitimate app change) rather than 8080 being a typo/bug in the image itself. *Verify with the 1.9.0 release notes or Dockerfile before assuming the fix is "update the Service" rather than "roll back the image."*
- **Assumed:** `/live` and `/ready` behave as documented (process-only vs dependency-aware) in the actual 1.9.0 binary. *Verify by curling both endpoints on a pod directly (`kubectl exec` + `curl localhost:8080/live` and `/ready`) rather than trusting the doc contract alone.*
- **Assumed:** the Postgres "warming_up" state is transient (seconds) and self-resolves, as the log timeline suggests (`503` at `14:21:12` → `200` at `14:21:19`). *Verify there isn't a connection-pool exhaustion or DB-side capacity issue that would make warm-up open-ended.*
- **Assumed:** 128Mi is simply undersized rather than a genuine memory leak in 1.9.0. *Verify by watching RSS over a longer soak window after raising the limit — if memory climbs unbounded rather than plateauing, this is a leak, not a sizing problem, and the image itself needs a fix.*
- **Assumed:** no other consumer of the `orders-api` Service (e.g., another ingress rule, a NetworkPolicy) hardcodes port `8081`. *Grep for `8081` across the namespace's manifests before changing the Service port.*
