# AI Usage

**Tool used:** Claude (Anthropic), in an agentic coding environment with a sandboxed shell.

## Interaction summary

I gave Claude the my findings and  incident packet and asked it to (a) validate my root cause if it is correcr or not, (b) produce a
corrected manifest

Representative prompts:
- "Given this evidence, what are the distinct root causes vs. symptoms? Don't stop at 'the
  deployment is unhealthy' — I need the specific config/operational faults."
- "Write a corrected Deployment + Service that fixes routing, probes, resources, the rollout
  strategy, and the plaintext credential, without deleting all pods or bypassing health checks."
- "Write a Python validator that checks targetPort-vs-containerPort matching, probe path
  allow-listing, plaintext-credential patterns in env values, and resource sanity — no
  cluster access, pure YAML parsing."

## What I accepted, changed, or rejected

- **Accepted, with verification:** the core diagnosis (port mismatch, swapped/wrong probe
  paths, undersized memory limit, zero-surge rollout, plaintext secret). I cross-checked each
  claim against the literal evidence lines myself before writing it into
  `incident_analysis.md` — e.g. I re-derived the "8081→8080 port change" conclusion by
  comparing the old pod's `1/1 Ready` status against the ingress's TCP-level "connection
  refused" to `8081`, rather than accepting the AI's phrasing at face value.
- **Changed:** the first draft of the Service fix hardcoded `targetPort: 8080`. I changed it
  to reference the named port (`targetPort: http`) so the Service and Deployment can't drift
  out of sync again if the container port ever changes — this is exactly the class of bug
  that caused the incident, so I wanted the fix to be structurally resistant to a repeat, not
  just numerically correct today.
- **Changed:** the first draft's memory limit was a round guess (`256Mi`). I asked for the
  reasoning to be tied explicitly to the observed `top pod` numbers (up to 176Mi) and
  recalculated the headroom myself (320Mi ≈ +80% over the highest observed sample) rather
  than accepting an unexplained number.
- **Rejected:** an early suggestion to add `failureThreshold: 10` to liveness as a quick fix
  for the CrashLoopBackOff, instead of fixing the actual liveness *path*. That would have
  masked the dependency-coupling bug rather than fixing it, and conflicts with the exercise's
  constraint not to bypass health checks as the permanent fix — so it's a symptom patch, not
  a root-cause fix, and I discarded it.
- **Rejected/softened:** a suggestion to set `maxUnavailable: 0, maxSurge: 2` for extra
  headroom. I kept surge at 1 — enough to guarantee no availability drop, without assuming
  the cluster/node pool has spare capacity for 2 extra pods, which wasn't in evidence.

## How I verified AI-generated artifacts

- **YAML:** manually re-read the full manifest line by line against the Evidence A/B blocks
  to confirm every original field was either intentionally preserved or intentionally changed
  for a stated reason (no silent drops).
- **Script:** actually executed `tests/validate.py` in this sandbox against (1) a
  reconstruction of the *original broken* manifest and (2) the remediated manifest, and
  confirmed it fails with the three expected findings on the former and passes cleanly on the
  latter (see terminal output captured during the session). I did not just trust that the
  script "should" work.
- **Commands in the runbook:** checked each `kubectl` command for syntactically valid flags
  and namespace consistency; flagged (in the runbook itself) the one command whose safety is
  conditional — patching the Service `targetPort` back to 8081 during rollback — as something
  to verify against the actually-running image before executing, rather than presenting it as
  unconditionally safe.
- **Root cause claims:** every claim in `incident_analysis.md` is tied to a specific line in
  Evidence A–D; I did not include a claim that wasn't traceable to the given evidence.

If I had chosen not to use AI at all, I'd have used the same verification discipline: draft
the diagnosis myself, then have an assistant red-team it for anything not supported by the
evidence, and independently execute or dry-run any generated command/manifest before treating
it as trustworthy — AI output goes through the same scrutiny as a suggestion from a teammate
I haven't worked with before, not more and not less.
