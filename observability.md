# Observability and Prevention Plan

## Metrics / alerts for this incident pattern

1. **Ingress/upstream connection errors by upstream port** — `sum(rate(ingress_upstream_errors_total{service="orders-api",reason="connection_refused"}[5m]))`. This would have caught Root Cause 1 (Service/port mismatch) within a minute of rollout, before customer-visible 502s accumulated.
2. **Container OOM kill count** — `sum(increase(kube_pod_container_status_restarts_total{namespace="production",container="orders-api"}[15m])) by (pod)` joined with `kube_pod_container_status_last_terminated_reason="OOMKilled"`. Direct signal for Root Cause 3.
3. **Readiness probe failure rate per pod** — `rate(probe_http_requests_total{namespace="production",pod=~"orders-api.*",probe="readiness",code!~"2.."}[5m])`. Catches misconfigured probe paths immediately on rollout, before pods ever get marked unhealthy by Kubernetes.
4. **Memory utilization vs. limit (headroom)** — `container_memory_working_set_bytes{container="orders-api"} / container_spec_memory_limit_bytes{container="orders-api"}`. Alert on sustained >85% rather than waiting for the OOM itself.
5. **Available replicas vs. desired** — `kube_deployment_status_replicas_available{deployment="orders-api"} < kube_deployment_spec_replicas{deployment="orders-api"}` sustained for >2 minutes. Cheap, high-signal indicator that a rollout is not converging.

## SLO-oriented alert (avoids per-pod paging noise)

**Fast-burn error-budget alert**, not "pod X restarted":

```
Alert: OrdersAPIFastErrorBudgetBurn
Expr: (
  sum(rate(http_requests_total{service="orders-api",code=~"5.."}[5m]))
  /
  sum(rate(http_requests_total{service="orders-api"}[5m]))
) > (14.4 * (1 - 0.9995))   # 14.4x burn rate = exhausts a 30-day budget in ~1 day if sustained
For: 2m
Severity: page
```

This pages on customer-facing impact against the stated 99.95% target, regardless of which pod or root cause is involved, and won't fire for a single pod restarting normally during a healthy rolling update. Pair it with a slow-burn variant (e.g. 6x burn rate over 1h, ticket not page) to catch slower degradations.

## Dashboard / log fields that shorten time-to-diagnosis

- Per-request structured log fields already present in Evidence D are good — keep and standardize: `path`, `status`, `latency_ms`, `dependency`, `dependency_state`. Add `pod_name`, `image_tag`, and `rollout_revision` to every log line so a bad rollout is instantly correlatable across pods.
- Dashboard panel: **readiness/liveness pass-rate over time, split by ReplicaSet/image tag** — makes an "old RS healthy, new RS failing" pattern (exactly this incident) visually obvious in seconds.
- Dashboard panel: **Service endpoint count over time** — a drop to fewer endpoints than desired replicas is a fast routing-health signal.
- Dashboard panel: **memory working set vs. limit per pod, annotated with deploy events** — makes "OOM started right after this rollout" obvious without cross-referencing timestamps manually.

## Preventive CI/CD check

Add a manifest-diff/policy gate to the deploy pipeline that fails the build (not just warns) if:

- `Service.spec.ports[].targetPort` does not match a `containerPort` (by number or by name) declared in the corresponding Deployment's pod template.
- Any `env` value matches a credential-like pattern (`://.*:.*@`) instead of using `valueFrom.secretKeyRef`.
- `readinessProbe`/`livenessProbe` paths aren't in an allow-list of documented app endpoints (`/live`, `/ready` here), or are missing entirely.
- `resources.requests`/`resources.limits` are missing, or `limits.memory` is unset/zero.

This is exactly what `tests/validate.py` in this repo implements, runnable locally and wired as a CI step — see README for the single command to run it.
