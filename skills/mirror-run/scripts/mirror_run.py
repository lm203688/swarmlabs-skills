#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mirror_run.py — virtual <-> physical experiment bridge for scientific agents.

Builds a GP surrogate in silico from observed (train) data, predicts at query
points with uncertainty, gates each point behind the VirtualLab Guard, and
(optionally) executes the same design on a real MHS-compatible endpoint to
quantify the sim-to-real gap.

If --real-endpoint is omitted, a *virtual twin* (perturbed by the scenario
noise) is used to illustrate the gap. The twin is explicitly labeled; no real
measurement is ever fabricated.
"""
import argparse
import json
import sys
import urllib.request
import urllib.error

API = "https://swarmlabs.tools/api/v3"
TIMEOUT = 30
UA = "SwarmLabs-MirrorRun/1.0 (+https://swarmlabs.tools)"


def _request(path, payload=None):
    url = API + path
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers,
                                method="POST" if payload is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        raise SystemExit("API %s failed: HTTP %s\n%s" % (path, exc.code, body))
    except Exception as exc:  # network / TLS
        raise SystemExit("API %s unreachable: %s" % (path, exc))


def _as_matrix(v):
    """Accept a flat list or a list-of-lists; always return list-of-lists."""
    if not v:
        return []
    if isinstance(v[0], (int, float)):
        return [[x] for x in v]
    return [list(x) for x in v]


def main():
    ap = argparse.ArgumentParser(description="Mirror virtual experiments to real instruments with UQ.")
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--train-x-json", required=True, help='e.g. [[0.1],[0.5],[1.0]] or [0.1,0.5,1.0]')
    ap.add_argument("--train-y-json", required=True, help='e.g. [0.11,0.26,0.28]')
    ap.add_argument("--query-x-json", required=True)
    ap.add_argument("--real-endpoint", default=None,
                    help="MHS-compatible URL returning {\"y\": <number>} per POST {x:[..]}")
    ap.add_argument("--emit-mhs", action="store_true", help="also print an MHS-ready command manifest")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    train_x = _as_matrix(json.loads(args.train_x_json))
    train_y = json.loads(args.train_y_json)
    query_x = _as_matrix(json.loads(args.query_x_json))
    if len(train_x) != len(train_y):
        raise SystemExit("--train-x-json and --train-y-json length mismatch")

    # 1) Virtual GP surrogate
    pred = _request("/predict", {"train_x": train_x, "train_y": train_y,
                                 "query_x": query_x, "noise_rel": 0.03})
    means = pred.get("mean") or []
    stds = pred.get("std") or []

    # 2) Guard gate (field is `status`, not `verdict`)
    guard = {}
    try:
        guard = _request("/guard", {"scenario": args.scenario,
                                    "train_x": train_x, "train_y": train_y,
                                    "query_x": query_x})
    except SystemExit:
        guard = {}  # guard unavailable -> surface, don't fake a verdict
    statuses = guard.get("status") or guard.get("statuses") or []
    trusted_ratio = guard.get("trusted_ratio")

    # 3) Real side (or virtual twin)
    real_vals = []
    used_real = bool(args.real_endpoint)
    for q in query_x:
        if used_real:
            try:
                r = _request(args.real_endpoint.replace(API, ""), {"x": q}) if \
                    args.real_endpoint.startswith(API) else _post_external(args.real_endpoint, q)
                real_vals.append(float(r.get("y")))
                continue
            except SystemExit as e:
                sys.stderr.write("real-endpoint failed for %s: %s\n" % (q, e))
                used_real = False  # fall back to twin for remaining points
        # virtual twin: virtual mean + scenario-ish noise
        import random
        twin_noise = (stds[len(real_vals)] if len(real_vals) < len(stds) else 0.05)
        real_vals.append(means[len(real_vals)] + random.uniform(-1, 1) * twin_noise)

    # 4) sim2real delta
    delta = [abs(m - r) for m, r in zip(means, real_vals)]
    sim2real = sum(delta) / len(delta) if delta else 0.0

    report = {
        "scenario": args.scenario,
        "mode": "real-instrument" if used_real else "virtual-twin (no --real-endpoint)",
        "n_query": len(query_x),
        "virtual_mean": means,
        "virtual_std": stds,
        "real_or_twin": real_vals,
        "sim2real_delta": round(sim2real, 6),
        "guard": {"status": statuses, "trusted_ratio": trusted_ratio},
    }

    if args.emit_mhs:
        report["mhs_manifest"] = {
            "virtual_instrument": "swarmlabs.v3",
            "scenario": args.scenario,
            "real_adapter": {
                "mhs_device": "tecan_fluent_or_equivalent",
                "command": "pipette(S, well); incubate(37C); read(OD600)",
                "guard": (statuses[0] if statuses else "unknown"),
                "trusted_ratio": trusted_ratio,
            },
            "next_points": query_x,
            "note": "Execute only where guard.status == 'pass'.",
        }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print("Scenario : %s" % args.scenario)
    print("Mode     : %s" % report["mode"])
    print("Guard    : %s  (trusted_ratio=%s)" % (statuses, trusted_ratio))
    print("sim2real : %s" % report["sim2real_delta"])
    print()
    print("  %-22s %-10s %-10s %-10s" % ("query_x", "virtual", "std", "real/twin"))
    for q, m, s, rv in zip(query_x, means, stds, real_vals):
        print("  %-22s %-10s %-10s %-10s" % (str(q), round(m, 4), round(s, 4), round(rv, 4)))
    if args.emit_mhs:
        print("\nMHS manifest:\n" + json.dumps(report["mhs_manifest"], ensure_ascii=False, indent=2))


def _post_external(url, x):
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    data = json.dumps({"x": x}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


if __name__ == "__main__":
    main()
