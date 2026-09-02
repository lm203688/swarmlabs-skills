#!/usr/bin/env python3
"""Suggest the next experiment to run, with uncertainty and OOD guard.

Standard library only -- no third-party dependencies.

Examples
--------
Exploit (highest predicted mean), using 5 existing points::

    python design_next_experiment.py \
        --scenario microbio_monod \
        --train-x 0.1 0.5 1.0 2.0 5.0 \
        --train-y 0.15 0.42 0.58 0.71 0.79 \
        --strategy balanced --top 3

Explore (highest uncertainty), 2D scenario::

    python design_next_experiment.py \
        --scenario surrogate_branin \
        --train-x-json '[[0,0],[1,1],[2,2]]' \
        --train-y-json '[1.0, 2.0, 3.0]' \
        --strategy explore
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

API = "https://swarmlabs.tools/api/v3"
TIMEOUT = 30
# Cloudflare bot protection returns HTTP 403 / "error code: 1010" for the
# default Python-urllib User-Agent, so always send an identifiable one.
USER_AGENT = "swarmlabs-agent-skill/1.0 (+https://swarmlabs.tools)"

VERDICT_OK = {"pass", "controlled"}


def _request(path: str, payload: dict | None = None) -> dict:
    """Call the v3 API.

    A User-Agent header is mandatory: Cloudflare's bot protection rejects the
    default ``Python-urllib/3.x`` UA with HTTP 403 / error code 1010.
    """
    url = API + path
    headers = {"User-Agent": USER_AGENT}
    if payload is None:
        req = urllib.request.Request(url, headers=headers, method="GET")
    else:
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:400]
        if exc.code == 403:
            raise SystemExit(
                "API %s blocked by Cloudflare (HTTP 403).\n"
                "If the response mentions 'error code: 1010', the request User-Agent\n"
                "was rejected. This script sets one; check whether a proxy is\n"
                "rewriting it.\n%s" % (path, body)
            )
        raise SystemExit("API %s failed: HTTP %s\n%s" % (path, exc.code, body))
    except urllib.error.URLError as exc:
        raise SystemExit(
            "Cannot reach %s (%s). This skill needs network access." % (url, exc.reason)
        )


def get_scenario(key: str) -> dict:
    data = _request("/scenarios")
    for s in data.get("scenarios", []):
        if s.get("key") == key:
            return s
    keys = ", ".join(s.get("key", "?") for s in data.get("scenarios", []))
    raise SystemExit(
        "Scenario '%s' not found on the live API.\n"
        "Available (%d): %s\n"
        "The 52 microbiology scenarios (micro_*) are catalogued in\n"
        "references/microbiology-scenarios.md but are not deployed yet."
        % (key, data.get("count", 0), keys)
    )


def grid(bounds: list[list[float]], n: int) -> list[list[float]]:
    """Uniform grid over bounds. 1D and 2D supported; 3D+ uses coarse sampling."""
    dim = len(bounds)
    if dim == 1:
        lo, hi = bounds[0]
        step = (hi - lo) / (n - 1) if n > 1 else 0
        return [[round(lo + i * step, 6)] for i in range(n)]
    if dim == 2:
        (x0, x1), (y0, y1) = bounds
        m = max(2, int(n ** 0.5))
        sx = (x1 - x0) / (m - 1)
        sy = (y1 - y0) / (m - 1)
        return [
            [round(x0 + i * sx, 6), round(y0 + j * sy, 6)]
            for i in range(m)
            for j in range(m)
        ]
    # 3D+: random-ish deterministic lattice, capped for payload size
    import itertools

    per_dim = max(2, int(round(n ** (1.0 / dim))))
    axes = []
    for lo, hi in bounds:
        step = (hi - lo) / (per_dim - 1)
        axes.append([round(lo + k * step, 6) for k in range(per_dim)])
    return [list(p) for p in itertools.product(*axes)][:n]


def rank(cands: list[list[float]], pred: dict, guard: dict, strategy: str) -> list[dict]:
    means = pred.get("mean") or pred.get("means") or []
    stds = pred.get("std") or pred.get("stds") or []
    dists = pred.get("dist") or pred.get("dists") or []
    # /guard returns its verdicts under "status" (e.g. ["pass","controlled","reject"]).
    # Accept a few spellings so a rename on the API side never silently disables
    # the guard -- a missing verdict must not look like a passing one.
    verdicts = (
        guard.get("status")
        or guard.get("verdicts")
        or guard.get("verdict")
        or []
    )

    rows = []
    for i, x in enumerate(cands):
        mean = means[i] if i < len(means) else None
        std = stds[i] if i < len(stds) else None
        dist = dists[i] if i < len(dists) else None
        verdict = verdicts[i] if i < len(verdicts) else "unknown"
        rows.append(
            {
                "x": x,
                "mean": mean,
                "std": std,
                "dist": dist,
                "verdict": verdict,
                "lo": None if mean is None or std is None else mean - 1.96 * std,
                "hi": None if mean is None or std is None else mean + 1.96 * std,
            }
        )

    if not verdicts:
        # Guard unavailable: verdicts are "unknown", which is NOT a rejection.
        # Rank everything rather than implying the model failed.
        pool = rows
    else:
        pool = [r for r in rows if str(r["verdict"]).lower() in VERDICT_OK]
        if not pool:
            # Genuinely all out of distribution: surface the nearest candidates so the
            # user sees where a real experiment would help, instead of a guessed value.
            return sorted(rows, key=lambda r: (r["dist"] is None, r["dist"]))

    if strategy == "exploit":
        pool.sort(key=lambda r: -(r["mean"] if r["mean"] is not None else -1e9))
    elif strategy == "explore":
        pool.sort(key=lambda r: -(r["std"] if r["std"] is not None else -1e9))
    else:  # balanced
        top_mean = sorted(
            pool, key=lambda r: -(r["mean"] if r["mean"] is not None else -1e9)
        )
        top_std = sorted(
            pool, key=lambda r: -(r["std"] if r["std"] is not None else -1e9)
        )
        merged, seen = [], set()
        for a, b in zip(top_mean, top_std):
            for r in (a, b):
                key = tuple(r["x"])
                if key not in seen:
                    seen.add(key)
                    merged.append(r)
        pool = merged
    return pool


def fmt(v, nd: int = 4) -> str:
    if v is None:
        return "-"
    if isinstance(v, (int, float)):
        return ("%." + str(nd) + "f") % v
    return str(v)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Suggest next experiments with GP uncertainty and OOD guard."
    )
    ap.add_argument("--scenario", required=True, help="Scenario key, e.g. microbio_monod")
    ap.add_argument("--train-x", nargs="+", type=float, help="1D training inputs")
    ap.add_argument("--train-y", nargs="+", type=float, help="Training observations")
    ap.add_argument("--train-x-json", help="N-D training inputs as JSON array")
    ap.add_argument("--train-y-json", help="N-D training outputs as JSON array")
    ap.add_argument(
        "--strategy",
        choices=["exploit", "explore", "balanced"],
        default="balanced",
        help="exploit=highest mean, explore=highest uncertainty, balanced=mix",
    )
    ap.add_argument("--top", type=int, default=5, help="How many suggestions to print")
    ap.add_argument("--grid", type=int, default=60, help="Candidate grid size")
    ap.add_argument("--json", action="store_true", help="Emit raw JSON instead of a table")
    args = ap.parse_args()

    # ---- training data ----
    if args.train_x_json:
        train_x = json.loads(args.train_x_json)
    elif args.train_x is not None:
        train_x = [[v] for v in args.train_x]
    else:
        raise SystemExit("Provide --train-x (1D) or --train-x-json (N-D).")

    if args.train_y_json:
        train_y = json.loads(args.train_y_json)
    elif args.train_y is not None:
        train_y = list(args.train_y)
    else:
        raise SystemExit("Provide --train-y (1D) or --train-y-json (N-D).")

    if len(train_x) != len(train_y):
        raise SystemExit(
            "train_x/train_y length mismatch: %d vs %d" % (len(train_x), len(train_y))
        )
    if not train_x:
        raise SystemExit("Need at least one training point.")

    # ---- scenario bounds ----
    scen = get_scenario(args.scenario)
    bounds = scen.get("bounds")
    if not bounds:
        raise SystemExit("Scenario '%s' exposes no bounds." % args.scenario)
    if len(train_x[0]) != len(bounds):
        raise SystemExit(
            "Dimension mismatch: training data is %dD, scenario '%s' is %dD."
            % (len(train_x[0]), args.scenario, len(bounds))
        )

    cands = grid(bounds, max(2, args.grid))
    payload = {"scenario": args.scenario, "train_x": train_x, "train_y": train_y,
               "query_x": cands}

    pred = _request("/predict", payload)
    try:
        guard = _request("/guard", payload)
        guard_ok = bool(guard.get("status") or guard.get("verdicts"))
    except SystemExit:
        # Guard down: ranking still works, but verdicts must stay visibly "unknown".
        guard, guard_ok = {}, False

    rows = rank(cands, pred, guard, args.strategy)[: args.top]

    if args.json:
        print(json.dumps(
            {"scenario": args.scenario, "strategy": args.strategy,
             "n_candidates": len(cands), "suggestions": rows},
            indent=2))
        return 0

    print("Scenario : %s  (%s)" % (args.scenario, scen.get("domain", "")))
    print("Strategy : %s   Candidates scanned: %d" % (args.strategy, len(cands)))
    if guard_ok and "trusted_ratio" in guard:
        print("Guard    : trusted_ratio=%s over %d candidates"
              % (guard["trusted_ratio"], guard.get("n", len(cands))))
    print("")
    if not guard_ok:
        print("~~ Guard endpoint unavailable -- verdicts are UNKNOWN, not 'reject'.")
        print("~~ Predictions below are NOT out-of-distribution checked.")
        print("")
    else:
        rejected = [r for r in rows if str(r["verdict"]).lower() not in VERDICT_OK]
        if rows and len(rejected) == len(rows):
            print("!! Every candidate is OUT OF DISTRIBUTION for the current model.")
            print("!! Do not trust these numbers. Run a real experiment near the")
            print("!! closest candidate below to extend the reliable region.")
            print("")
        elif rejected:
            print("~~ %d/%d suggestions are out of distribution; prefer 'pass' rows."
                  % (len(rejected), len(rows)))
            print("")
    hdr = "%-22s %10s %10s %10s %10s  %s" % ("x", "mean", "std", "lo95", "hi95", "verdict")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        xs = ",".join(fmt(v, 3) for v in r["x"])
        print("%-22s %10s %10s %10s %10s  %s" % (
            xs, fmt(r["mean"]), fmt(r["std"]), fmt(r["lo"]), fmt(r["hi"]), r["verdict"]))
    print("")
    print("lo95/hi95 = mean +/- 1.96*std (95% interval).")
    print("verdict   = pass | controlled | reject (OOD guard).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
