#!/usr/bin/env python3
"""
Lightweight validator for orders-api Kubernetes manifests.

Checks implemented (covers 4 of the 5 suggested checks):
  1. Service targetPort matches a declared container port (by number or name).
  2. Probe paths (readiness/liveness/startup) are within the documented app
     contract (/live, /ready) -- flags things like /health which don't exist.
  3. No plaintext credentials embedded in env values (e.g. postgres://user:pass@host).
  4. Resource requests/limits are present and internally sensible
     (requests <= limits, limits > 0, memory/cpu both set).

Usage:
    python3 tests/validate.py solution/deployment-and-service.yaml

Exit code 0 = pass, 1 = one or more findings (printed to stdout).
Requires only PyYAML (pip install pyyaml --break-system-packages).
"""

import sys
import re
import yaml

ALLOWED_PROBE_PATHS = {"/live", "/ready", "/metrics"}
CREDENTIAL_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^:/@\s]+:[^@\s]+@")


def load_docs(path):
    with open(path) as f:
        return [d for d in yaml.safe_load_all(f) if d]


def find(docs, kind):
    return [d for d in docs if d.get("kind") == kind]


def check_target_port(docs, findings):
    services = find(docs, "Service")
    deployments = find(docs, "Deployment")
    if not services or not deployments:
        return
    container_ports = set()
    for dep in deployments:
        containers = dep.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        for c in containers:
            for p in c.get("ports", []):
                if "containerPort" in p:
                    container_ports.add(p["containerPort"])
                if "name" in p:
                    container_ports.add(p["name"])
    for svc in services:
        for p in svc.get("spec", {}).get("ports", []):
            tp = p.get("targetPort")
            if tp not in container_ports:
                findings.append(
                    f"[targetPort] Service '{svc.get('metadata', {}).get('name')}' "
                    f"targetPort={tp!r} does not match any container port {sorted(container_ports, key=str)} "
                    f"in a Deployment in this file."
                )


def check_probe_paths(docs, findings):
    for dep in find(docs, "Deployment"):
        containers = dep.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        for c in containers:
            for probe_name in ("readinessProbe", "livenessProbe", "startupProbe"):
                probe = c.get(probe_name)
                if not probe:
                    if probe_name != "startupProbe":
                        findings.append(
                            f"[probe] Container '{c.get('name')}' is missing {probe_name}."
                        )
                    continue
                path = probe.get("httpGet", {}).get("path")
                if path is None:
                    continue
                if path not in ALLOWED_PROBE_PATHS:
                    findings.append(
                        f"[probe] Container '{c.get('name')}' {probe_name} path={path!r} "
                        f"is not in the documented app contract {sorted(ALLOWED_PROBE_PATHS)}."
                    )


def check_plaintext_credentials(docs, findings):
    for dep in find(docs, "Deployment"):
        containers = dep.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        for c in containers:
            for e in c.get("env", []):
                value = e.get("value")
                if value and CREDENTIAL_PATTERN.search(value):
                    findings.append(
                        f"[secret] Container '{c.get('name')}' env '{e.get('name')}' appears to "
                        f"contain a plaintext credential in a literal value. Use valueFrom.secretKeyRef instead."
                    )


def check_resources(docs, findings):
    for dep in find(docs, "Deployment"):
        containers = dep.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        for c in containers:
            res = c.get("resources", {})
            requests = res.get("requests", {})
            limits = res.get("limits", {})
            for field in ("cpu", "memory"):
                if field not in requests:
                    findings.append(f"[resources] Container '{c.get('name')}' missing requests.{field}.")
                if field not in limits:
                    findings.append(f"[resources] Container '{c.get('name')}' missing limits.{field}.")

            def parse_qty(q):
                # very small subset parser: handles Mi/Gi/m suffixes well enough for sanity checks
                if q is None:
                    return None
                m = re.match(r"^([0-9.]+)([a-zA-Z]*)$", str(q))
                if not m:
                    return None
                num, suffix = float(m.group(1)), m.group(2)
                mult = {"": 1, "m": 1e-3, "Mi": 2**20, "Gi": 2**30, "Ki": 2**10}.get(suffix)
                return num * mult if mult else None

            for field in ("cpu", "memory"):
                rq, lim = parse_qty(requests.get(field)), parse_qty(limits.get(field))
                if rq is not None and lim is not None and rq > lim:
                    findings.append(
                        f"[resources] Container '{c.get('name')}' requests.{field} ({requests.get(field)}) "
                        f"exceeds limits.{field} ({limits.get(field)})."
                    )
                if lim is not None and lim == 0:
                    findings.append(f"[resources] Container '{c.get('name')}' limits.{field} is zero.")


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <manifest.yaml>")
        sys.exit(2)

    docs = load_docs(sys.argv[1])
    findings = []
    check_target_port(docs, findings)
    check_probe_paths(docs, findings)
    check_plaintext_credentials(docs, findings)
    check_resources(docs, findings)

    if findings:
        print(f"FAIL — {len(findings)} finding(s):\n")
        for f in findings:
            print(f" - {f}")
        sys.exit(1)
    else:
        print("PASS — no issues found by validate.py")
        sys.exit(0)


if __name__ == "__main__":
    main()
