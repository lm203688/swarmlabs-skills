#!/usr/bin/env python3
"""Offline out-of-distribution gate: is this query inside the region we measured?

Mirrors the logic of the SwarmLabs /guard endpoint without requiring network
access. Pure numpy, no scipy, no credentials.

Method
------
1. Standardize inputs (z-scores) so distance is not dominated by the widest axis.
2. Compute the nearest-neighbour distance of every training point — this
   characterises how large the "holes" in the existing design already are.
3. For each query, measure its nearest-neighbour distance to the training set
   and compare against that reference scale.

    d <= ref            -> pass        (no worse than the existing design)
    ref < d <= 2 * ref  -> controlled  (usable, flag the wider interval)
    d > 2 * ref         -> reject      (do not trust; run a bridging experiment)

The reference scale is the largest nearest-neighbour gap in the training data,
i.e. the biggest hole the model already had to interpolate across. A query
farther from any data than that is asking for extrapolation.

Usage:
    python guard_local.py data.csv --query "37,7.0,6.0"
    python guard_local.py data.csv --query "37,7.0,6.0" --query "45,7.0,6.0"
    python guard_local.py data.csv --query-file queries.csv --json

CSV format matches the uq-coverage-audit skill: last column is y, preceding
columns are inputs. Header optional.

Exit codes:
    0  at least one query passed
    1  every query is outside the trusted region (or input error)
"""

from __future__ import annotations

import argparse
import csv
import json
import sys

import numpy as np

CONTROLLED_FACTOR = 2.0


def load_training(path: str) -> tuple[np.ndarray, np.ndarray, list[str]]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = [r for r in csv.reader(fh) if r and any(c.strip() for c in r)]

    if len(rows) < 3:
        raise ValueError(f"need at least 3 training rows, got {len(rows)}")

    header: list[str] = []
    try:
        float(rows[0][-1])
    except ValueError:
        header = rows[0]
        rows = rows[1:]

    arr = np.array([[float(c) for c in r] for r in rows], dtype=float)
    return arr[:, :-1], arr[:, -1], header


def parse_queries(args) -> tuple[np.ndarray, list[str]]:
    """Accept --query "a,b,c" (repeatable) and/or --query-file."""
    raw: list[list[float]] = []

    for q in args.query or []:
        raw.append([float(v) for v in q.replace(";", ",").split(",")])

    if args.query_file:
        with open(args.query_file, newline="", encoding="utf-8-sig") as fh:
            for row in csv.reader(fh):
                if row and any(c.strip() for c in row):
                    try:
                        raw.append([float(v) for v in row])
                    except ValueError:
                        continue  # skip header lines

    if not raw:
        raise ValueError("no queries supplied; use --query or --query-file")

    width = len(raw[0])
    bad = [r for r in raw if len(r) != width]
    if bad:
        raise ValueError(
            f"all queries must have the same width; expected {width}, got {[len(b) for b in bad]}"
        )
    return np.array(raw, dtype=float), [f"q{i}" for i in range(len(raw))]


def nn_distances(a: np.ndarray, b: np.ndarray, exclude_self: bool = False) -> np.ndarray:
    """Distance from each row of a to its nearest row of b.

    When a and b are the same set, the nearest neighbour of a row is itself
    (distance 0). Pass exclude_self=True to ignore the diagonal — required when
    measuring how large the gaps inside a training design already are.
    """
    sq = np.sum(a**2, axis=1)[:, None] + np.sum(b**2, axis=1)[None, :] - 2.0 * a @ b.T

    if exclude_self:
        if a.shape[0] != b.shape[0]:
            raise ValueError("exclude_self requires a and b to have the same rows")
        np.fill_diagonal(sq, np.inf)

    return np.sqrt(np.maximum(np.min(sq, axis=1), 0.0))


def classify(x_train: np.ndarray, x_query: np.ndarray) -> dict:
    mu, sd = x_train.mean(axis=0), x_train.std(axis=0)
    sd[sd < 1e-12] = 1.0
    z_train = (x_train - mu) / sd
    z_query = (x_query - mu) / sd

    # Reference scale: the largest hole already present in the training design.
    # exclude_self is essential — otherwise every point's nearest neighbour is
    # itself, the distance is 0, and the reference scale collapses.
    train_nn = nn_distances(z_train, z_train, exclude_self=True)
    ref = float(np.max(train_nn))
    if ref < 1e-9:  # fully duplicated rows
        ref = 1.0

    query_nn = nn_distances(z_query, z_train)

    statuses, ratios = [], []
    for d in query_nn:
        ratio = float(d) / ref
        ratios.append(round(ratio, 4))
        if d <= ref:
            statuses.append("pass")
        elif d <= CONTROLLED_FACTOR * ref:
            statuses.append("controlled")
        else:
            statuses.append("reject")

    counts = {s: statuses.count(s) for s in ("pass", "controlled", "reject")}
    trusted = counts["pass"] / len(statuses)

    return {
        "n_train": len(x_train),
        "n_query": len(x_query),
        "reference_distance": round(ref, 4),
        "distance": [round(float(d), 4) for d in query_nn],
        "distance_ratio": ratios,
        "status": statuses,
        "counts": counts,
        "trusted_ratio": round(trusted, 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv", help="training CSV; last column is y")
    ap.add_argument("--query", action="append", metavar="CSV_VALUES",
                    help='query point as "v1,v2,..."; repeatable')
    ap.add_argument("--query-file", help="CSV of query rows (inputs only)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        x_train, _, header = load_training(args.csv)
        x_query, labels = parse_queries(args)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if x_train.shape[1] != x_query.shape[1]:
        print(
            f"error: training data has {x_train.shape[1]} input columns but "
            f"queries have {x_query.shape[1]}",
            file=sys.stderr,
        )
        return 1

    res = classify(x_train, x_query)
    res["query_labels"] = labels
    if header:
        res["columns"] = header

    advice = {
        "pass": "inside the measured region; report the prediction normally",
        "controlled": "near the boundary; usable, but state the wider interval",
        "reject": "out of distribution; do NOT present as a result",
    }

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print("Out-of-Distribution Guard (offline)")
        print("-" * 58)
        print(f"  training points      : {res['n_train']}")
        print(f"  reference distance   : {res['reference_distance']:.4f} "
              f"(largest gap in the existing design)")
        print("-" * 58)
        for lbl, d, r, st in zip(labels, res["distance"], res["distance_ratio"], res["status"]):
            print(f"  {lbl:<6} d={d:<8.4f} ratio={r:<7.2f}  {st.upper()}")
            print(f"         {advice[st]}")
        print("-" * 58)
        c = res["counts"]
        print(f"  pass={c['pass']}  controlled={c['controlled']}  reject={c['reject']}"
              f"   trusted_ratio={res['trusted_ratio']:.2f}")
        if c["reject"]:
            print("\n  At least one condition is out of distribution.")
            print("  Add a bridging point roughly midway between the nearest")
            print("  training point and the rejected query, then re-run.")

    return 0 if res["counts"]["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
