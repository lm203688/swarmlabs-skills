#!/usr/bin/env python3
"""Audit whether a surrogate model's uncertainty intervals actually cover the truth.

A model can report a 95% interval and still be wrong 40% of the time. This script
measures the *empirical* coverage by leave-one-out cross validation, and compares
it against the nominal 95% claim.

Pure numpy. No scipy, no network, no credentials.

Usage:
    python uq_audit.py data.csv
    python uq_audit.py data.csv --nominal 0.95 --noise-floor 0.03
    python uq_audit.py data.csv --json

CSV format: one row per observation, last column is the target y, all preceding
columns are inputs. A header row is optional.

Exit codes:
    0  coverage verdict healthy or marginal
    1  overconfident (coverage far below nominal) or input error
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np

# The noise floor is deliberate. Removing it makes intervals narrower and
# coverage worse. Do not "tune" it away to make the report look better.
DEFAULT_NOISE_FLOOR = 0.03


def load_csv(path: str) -> tuple[np.ndarray, np.ndarray, list[str]]:
    import csv

    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = [r for r in csv.reader(fh) if r and any(c.strip() for c in r)]

    if len(rows) < 6:
        raise ValueError(
            f"need at least 6 rows for leave-one-out, got {len(rows)}"
        )

    header: list[str] = []
    try:
        float(rows[0][-1])
    except ValueError:
        header = rows[0]
        rows = rows[1:]

    try:
        arr = np.array([[float(c) for c in r] for r in rows], dtype=float)
    except ValueError as exc:
        raise ValueError(f"non-numeric cell in CSV: {exc}") from exc

    return arr[:, :-1], arr[:, -1], header


def standardize(x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mu = x.mean(axis=0)
    sd = x.std(axis=0)
    sd[sd < 1e-12] = 1.0
    return (x - mu) / sd, mu, sd


def rbf_kernel(a: np.ndarray, b: np.ndarray, length: float, var: float) -> np.ndarray:
    sq = np.sum(a**2, axis=1)[:, None] + np.sum(b**2, axis=1)[None, :] - 2.0 * a @ b.T
    return var * np.exp(-0.5 * np.maximum(sq, 0.0) / (length**2))


def gp_predict(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_query: np.ndarray,
    length: float,
    var: float,
    noise: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Posterior mean and std of a zero-mean GP with an RBF kernel."""
    k = rbf_kernel(x_train, x_train, length, var)
    k += (noise**2 + 1e-8) * np.eye(len(x_train))
    try:
        l_chol = np.linalg.cholesky(k)
    except np.linalg.LinAlgError:
        k += 1e-6 * np.eye(len(x_train))
        l_chol = np.linalg.cholesky(k)

    alpha = np.linalg.solve(l_chol.T, np.linalg.solve(l_chol, y_train))
    ks = rbf_kernel(x_train, x_query, length, var)
    mean = ks.T @ alpha

    v = np.linalg.solve(l_chol, ks)
    var_post = var - np.sum(v**2, axis=0)
    return mean, np.sqrt(np.maximum(var_post, 0.0))


def auto_length(x: np.ndarray) -> float:
    """Median nearest-neighbour distance — a defensible default length scale."""
    n = len(x)
    if n < 2:
        return 1.0
    sq = np.sum(x**2, axis=1)[:, None] + np.sum(x**2, axis=1)[None, :] - 2.0 * x @ x.T
    np.fill_diagonal(sq, np.inf)
    d = np.sqrt(np.maximum(np.min(sq, axis=1), 0.0))
    return float(max(np.median(d), 1e-3))


def loo_coverage(
    x: np.ndarray,
    y: np.ndarray,
    nominal: float,
    noise_floor: float,
) -> dict:
    """Leave-one-out empirical coverage of the nominal interval."""
    n = len(y)
    y_scale = float(np.std(y)) or 1.0
    # Noise floor is relative to the signal scale, matching SwarmLabs' 3% rule.
    noise = noise_floor * y_scale

    covered, widths, z_scores, errors = [], [], [], []
    for i in range(n):
        mask = np.ones(n, dtype=bool)
        mask[i] = False
        x_tr, y_tr = x[mask], y[mask] - y[mask].mean()
        x_te = x[i : i + 1]

        length = auto_length(x_tr)
        mean, std = gp_predict(
            x_tr, y_tr, x_te, length=length, var=y_scale**2, noise=noise
        )
        pred = float(mean[0]) + y[mask].mean()
        sd = float(std[0])

        z = 1.959963985  # 97.5th percentile, nominal 95%
        lo, hi = pred - z * sd, pred + z * sd
        truth = float(y[i])

        covered.append(lo <= truth <= hi)
        widths.append(2 * z * sd)
        errors.append(abs(pred - truth))
        if sd > 0:
            z_scores.append((truth - pred) / sd)

    cov = float(np.mean(covered))
    return {
        "n_points": n,
        "nominal": nominal,
        "empirical_coverage": round(cov, 4),
        "gap": round(cov - nominal, 4),
        "mean_interval_width": round(float(np.mean(widths)), 6),
        "mae": round(float(np.mean(errors)), 6),
        "rmse": round(float(np.sqrt(np.mean(np.square(errors)))), 6),
        "noise_floor_used": noise_floor,
        "std_z": round(float(np.std(z_scores)), 4) if z_scores else None,
    }


def verdict(cov: float, nominal: float) -> tuple[str, str]:
    gap = cov - nominal
    if gap >= -0.05:
        return "PASS", "Intervals are honest. Prediction bounds can be trusted as stated."
    if gap >= -0.15:
        return (
            "MARGINAL",
            "Intervals are somewhat optimistic. Widen the reported bounds before "
            "making decisions on them, or collect more data.",
        )
    return (
        "OVERCONFIDENT",
        "Intervals are far too narrow. Do NOT quote these bounds to a "
        "decision-maker — the model claims more certainty than it has. Add data, "
        "re-check the noise floor, or switch model form.",
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv", help="CSV file; last column is y")
    ap.add_argument("--nominal", type=float, default=0.95)
    ap.add_argument("--noise-floor", type=float, default=DEFAULT_NOISE_FLOOR)
    ap.add_argument("--json", action="store_true", help="emit machine-readable output")
    args = ap.parse_args()

    try:
        x_raw, y, header = load_csv(args.csv)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    x, _, _ = standardize(x_raw)
    res = loo_coverage(x, y, args.nominal, args.noise_floor)
    state, advice = verdict(res["empirical_coverage"], args.nominal)
    res["verdict"] = state
    res["advice"] = advice
    if header:
        res["columns"] = header

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print("Uncertainty Coverage Audit")
        print("-" * 46)
        print(f"  observations          : {res['n_points']}")
        print(f"  nominal coverage      : {res['nominal']:.2f}")
        print(f"  empirical coverage    : {res['empirical_coverage']:.2f}")
        print(f"  gap                   : {res['gap']:+.2f}")
        print(f"  mean interval width   : {res['mean_interval_width']:.4f}")
        print(f"  MAE / RMSE            : {res['mae']:.4f} / {res['rmse']:.4f}")
        print(f"  noise floor           : {res['noise_floor_used']}")
        print("-" * 46)
        print(f"  VERDICT: {state}")
        print(f"  {advice}")

    return 1 if state == "OVERCONFIDENT" else 0


if __name__ == "__main__":
    sys.exit(main())
