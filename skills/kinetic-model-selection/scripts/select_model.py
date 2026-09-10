#!/usr/bin/env python3
"""Fit competing growth-kinetics forms and pick the best-supported one offline.

Given (substrate -> specific growth rate) data, this fits eight published
kinetic forms, reports R^2, RMSE and AIC for each, and names the winner. When
two forms fit within noise, it prefers the simpler one — extra parameters have
to earn their keep.

Everything is local. No API key, no network, no account. numpy only.

Usage:
    python select_model.py data.csv
    python select_model.py data.csv --inhibition
    python select_model.py data.csv --json

CSV format: one row per observation. The first column is substrate
concentration S, the second is the observed rate (mu, or any rate you are
modelling). A header row is optional.

Exit codes:
    0  ranking produced
    1  input error
"""

from __future__ import annotations

import argparse
import csv
import json
import sys

import numpy as np

RNG_SEED = 20260909


def read_csv(path: str) -> tuple[np.ndarray, np.ndarray]:
    rows: list[list[float]] = []
    header: list[str] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for i, raw in enumerate(csv.reader(fh)):
            if not raw or all(c.strip() == "" for c in raw):
                continue
            try:
                vals = [float(c) for c in raw]
            except ValueError:
                if i == 0:
                    header = [c.strip() for c in raw]
                continue
            if len(vals) < 2:
                continue
            rows.append(vals[:2])
    if len(rows) < 5:
        raise ValueError(f"{path}: need at least 5 numeric rows, got {len(rows)}")
    arr = np.asarray(rows, dtype=float)
    return arr[:, 0], arr[:, 1]


# Each model: (name, function(S, params...), param names, rough lower/upper bounds)
def _safe(x):
    return np.where(np.isfinite(x), x, 0.0)


MODELS = {
    "monod": (
        "Monod",
        lambda S, mmax, Ks: mmax * S / (Ks + S),
        ["mu_max", "Ks"],
        [(1e-4, 10.0), (1e-5, 100.0)],
    ),
    "tessier": (
        "Tessier",
        lambda S, mmax, Ks: mmax * (1.0 - np.exp(-S / max(Ks, 1e-9))),
        ["mu_max", "Ks"],
        [(1e-4, 10.0), (1e-5, 100.0)],
    ),
    "moser": (
        "Moser",
        lambda S, mmax, Ks, n: mmax * S ** n / (Ks + S ** n),
        ["mu_max", "Ks", "n"],
        [(1e-4, 10.0), (1e-5, 100.0), (0.2, 4.0)],
    ),
    "blackman": (
        "Blackman",
        lambda S, mmax, Ks: np.minimum(mmax * S / (2.0 * max(Ks, 1e-9)), mmax),
        ["mu_max", "Ks"],
        [(1e-4, 10.0), (1e-5, 100.0)],
    ),
    "andrews": (
        "Andrews (substrate inhibition)",
        lambda S, mmax, Ks, Ki: mmax * S / (Ks + S + S * S / max(Ki, 1e-9)),
        ["mu_max", "Ks", "Ki"],
        [(1e-4, 10.0), (1e-5, 100.0), (1e-4, 1e4)],
    ),
    "haldane_product": (
        "Haldane (product inhibition)",
        lambda S, mmax, Ks, Kp: mmax * S / ((Ks + S) * (1.0 + S / max(Kp, 1e-9))),
        ["mu_max", "Ks", "Kp"],
        [(1e-4, 10.0), (1e-5, 100.0), (1e-4, 1e4)],
    ),
    "first_order": (
        "First order",
        lambda S, k: k * S,
        ["k"],
        [(1e-6, 100.0)],
    ),
    "constant": (
        "Zero order (constant)",
        lambda S, c: np.full_like(S, c),
        ["c"],
        [(1e-4, 10.0)],
    ),
}


def fit_model(
    key: str, S: np.ndarray, mu: np.ndarray, iters: int = 9000
) -> dict | None:
    """Random search followed by coordinate refinement.

    Deterministic: the RNG is seeded, so the same input always yields the same
    ranking. Cheap enough for 1-D kinetics and free of scipy.
    """
    name, fn, pnames, bounds = MODELS[key]
    lo = np.array([b[0] for b in bounds])
    hi = np.array([b[1] for b in bounds])
    rng = np.random.default_rng(RNG_SEED)

    def sse(p: np.ndarray) -> float:
        try:
            pred = _safe(fn(S, *p))
        except (FloatingPointError, ZeroDivisionError, OverflowError):
            return np.inf
        if not np.all(np.isfinite(pred)):
            return np.inf
        return float(((pred - mu) ** 2).sum())

    # Log-uniform sampling: kinetic constants span orders of magnitude.
    cand = np.exp(rng.uniform(np.log(lo), np.log(hi), size=(iters, len(lo))))
    scores = np.array([sse(p) for p in cand])
    best_i = int(np.argmin(scores))
    best = cand[best_i].copy()
    best_s = float(scores[best_i])
    if not np.isfinite(best_s):
        return None

    # Coordinate refinement: shrink the step until nothing improves.
    step = 0.5
    for _ in range(60):
        improved = False
        for j in range(len(best)):
            for factor in (1.0 - step, 1.0 + step):
                trial = best.copy()
                trial[j] = np.clip(trial[j] * (factor if factor > 0 else 0.5), lo[j], hi[j])
                s = sse(trial)
                if s < best_s - 1e-15:
                    best, best_s, improved = trial, s, True
        if not improved:
            step *= 0.5
            if step < 1e-4:
                break

    n = len(mu)
    pred = _safe(fn(S, *best))
    ss_res = float(((mu - pred) ** 2).sum())
    ss_tot = float(((mu - mu.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    rmse = float(np.sqrt(ss_res / n))
    k = len(best) + 1  # parameters + variance
    # AIC for least squares (small-sample corrected).
    aic = n * np.log(ss_res / n) + 2 * k + (2 * k * (k + 1)) / max(n - k - 1, 1)

    return {
        "key": key,
        "model": name,
        "params": {p: float(v) for p, v in zip(pnames, best)},
        "r2": float(r2),
        "rmse": rmse,
        "aic": float(aic),
        "n_params": len(best),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Fit competing kinetic models and rank them."
    )
    ap.add_argument("data", help="CSV of substrate,rate")
    ap.add_argument(
        "--inhibition",
        action="store_true",
        help="Also fit substrate/product inhibition forms (default: all)",
    )
    ap.add_argument(
        "--no-inhibition",
        action="store_true",
        help="Skip inhibition forms",
    )
    ap.add_argument("--json", action="store_true", help="Emit JSON")
    args = ap.parse_args()

    try:
        S, mu = read_csv(args.data)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if np.any(S < 0):
        print("error: negative substrate concentrations", file=sys.stderr)
        return 1

    keys = list(MODELS)
    if args.no_inhibition:
        keys = [k for k in keys if k not in ("andrews", "haldane_product")]

    results = []
    for k in keys:
        r = fit_model(k, S, mu)
        if r and np.isfinite(r["r2"]):
            results.append(r)
    if not results:
        print("error: no model could be fitted to this data", file=sys.stderr)
        return 1

    results.sort(key=lambda r: r["aic"])
    best = results[0]
    best_r2 = best["r2"]
    # Prefer the simpler model when the fit is within noise of the best.
    for r in results:
        if r["r2"] >= best_r2 - 0.005 and r["n_params"] < best["n_params"]:
            best = r
            break

    payload = {
        "observations": int(len(mu)),
        "substrate_range": [float(S.min()), float(S.max())],
        "winner": best["model"],
        "winner_criterion": "lowest AIC, simplest model within 0.005 R^2 of best",
        "ranking": results,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    print("Kinetic Model Selection")
    print("-" * 66)
    print(f"  observations      : {len(mu)}")
    print(f"  substrate range   : {S.min():.4g} .. {S.max():.4g}")
    print("-" * 66)
    print(f"  {'model':<32}{'R2':>8}{'RMSE':>10}{'AIC':>10}{'n':>4}")
    for r in results:
        mark = " <=" if r["model"] == best["model"] else ""
        print(
            f"  {r['model']:<32}{r['r2']:>8.4f}{r['rmse']:>10.4f}"
            f"{r['aic']:>10.2f}{r['n_params']:>4}{mark}"
        )
    print("-" * 66)
    print(f"  Selected: {best['model']}")
    params = ", ".join(f"{k}={v:.5g}" for k, v in best["params"].items())
    print(f"  Fitted  : {params}")
    print()
    print("  R2 ties within 0.005 go to the model with fewer parameters.")
    print("  A selected form is a hypothesis about mechanism, not proof of it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
