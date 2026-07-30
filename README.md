# orders-api Incident Response — Submission

## What I completed

**Must Complete (all 4):**
- `incident_analysis.md` — 5 distinct root causes (Service/port mismatch, inverted/wrong
  probe paths, undersized memory limit, zero-surge rollout strategy, plaintext credential),
  each tied to specific evidence lines, plus first-checks and stated assumptions to verify.
- `solution/deployment-and-service.yaml` — corrected Service, Deployment, and an added
  PodDisruptionBudget. Uses a named `targetPort` (not a hardcoded number) so the routing bug
  class can't silently recur; probes split correctly between `/live` (liveness) and `/ready`
  (readiness) with an added `startupProbe`; resources resized off the observed `top pod` data;
  rollout changed to `maxSurge:1/maxUnavailable:0`; DB credential moved to a `secretKeyRef`
  (no real secret value included, per the constraint).
- `runbook.md` — pre-deploy validation, rollout sequence, post-deploy health/service checks,
  explicit success/failure signals, rollback trigger and procedure (including a caveat about
  the one rollback command whose safety is conditional).
- `AI_USAGE.md` — tool, prompts, what was accepted/changed/rejected and why, and how each
  artifact was actually verified (including running the validator against both the broken and
  fixed manifests in this session).

**Should Attempt (both):**
- `observability.md` — 5 metrics/alerts tied directly to this incident's root causes, one
  SLO-based fast-burn alert (error-budget burn rate against the 99.95% target, not per-pod
  paging), dashboard/log field suggestions, and one CI/CD gate proposal.
- `tests/validate.py` — pure-Python (PyYAML only) validator covering 4 of the 5 suggested
  checks: targetPort-vs-containerPort match, probe-path allow-listing, plaintext-credential
  detection in env values, and resource requests/limits sanity. Tested against both the
  original broken manifest (fails with the 3 expected findings) and the remediated manifest
  (passes clean) — see `AI_USAGE.md` for the verification note.

**Stretch (both, as design docs rather than a running prototype, given the time budget):**
- `STRETCH.md` §7 — approval-gated AI triage workflow design with explicit safeguards for
  secrets, prompt injection in logs, hallucinated commands, and excessive permissions.
- `STRETCH.md` §8 — a controlled resilience test for the dependency-warm-up failure mode,
  with expected result and how it's isolated from live customer traffic.

## Assumptions made

- 1.9.0 intentionally changed the app's listen port from 8081 to 8080 (inferred from the old
  pod being healthy on 8081 and the new image's own log line confirming 8080); flagged as
  something to confirm against the release notes/Dockerfile before treating the fix as
  "update the Service" rather than "this is a regression, roll back."
- `/live` and `/ready` behave as documented in the actual 1.9.0 binary.
- Postgres warm-up is transient/self-resolving, not a symptom of a separate DB-side capacity
  issue.
- 128Mi was simply undersized, not evidence of a memory leak (should be confirmed with a
  longer soak test post-fix — flagged in `incident_analysis.md`).
- Full list of assumptions/uncertainties with verification steps is in
  `incident_analysis.md`.

## How to inspect or run the artifacts

```bash
# Validate the fixed manifest is well-formed and passes all checks
pip install pyyaml --break-system-packages
python3 tests/validate.py solution/deployment-and-service.yaml
# -> "PASS — no issues found by validate.py"

# (Optional) confirm the validator actually catches the original bugs:
# reconstruct the original broken manifest from the exercise's Evidence A/B and run:
python3 tests/validate.py <path-to-original-broken-manifest>
# -> FAIL with 3 findings: targetPort mismatch, /health probe path, plaintext DATABASE_URL

# Dry-run against a real cluster (requires kubectl context pointed at a test cluster)
kubectl apply --dry-run=server -f solution/deployment-and-service.yaml -n production
```

Read order: `incident_analysis.md` → `solution/deployment-and-service.yaml` → `runbook.md` →
`observability.md` → `tests/validate.py` → `AI_USAGE.md` → `STRETCH.md`.

## Time spent

~45 minutes on Must Complete, plus additional time on Should Attempt and Stretch sections.

## What I'd improve with more time

- Actually spin up a kind/minikube cluster to apply the manifest and reproduce the
  CrashLoopBackOff/OOM/502 chain live, rather than reasoning from the provided evidence —
  would let me confirm the exact `failureThreshold` timing math instead of estimating it.
- Extend `tests/validate.py` into a real CI workflow file (e.g. GitHub Actions) rather than
  just documenting it as a proposal, and add the schema-validation check (5th suggested check)
  via `kubeconform`.
- Prototype the §7 triage workflow as actual code (even a stub with mocked tool calls) rather
  than a design doc, to validate the allow-list/approval-gate mechanics concretely.
- Tune the memory limit against a real load/soak test instead of a static +80% headroom
  heuristic.
- Add a `NetworkPolicy` and `SecurityContext` (non-root, read-only root filesystem) — good
  hygiene, but out of scope for this specific incident so deprioritized under the time limit.
