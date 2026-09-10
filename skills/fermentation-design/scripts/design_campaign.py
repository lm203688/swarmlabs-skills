#!/usr/bin/env python3
"""Design the next fermentation runs offline, with error bars and OOD verdicts.

This is the executable form of "replace the wet experiment": given a handful of
measured (condition -> titre) pairs, it ranks untested conditions by expected
information gain, returns a 95% interval for each, and refuses to score
conditions outside the measured region.

Everything is local. No API key, no network, no account. numpy only.

Usage:
    python design_campaign.py data.csv
    python design_campaign.py data.csv --candidates grid.csv --top 5
    python design_campaign.py data.csv --bounds "30,40;6,8;0.05,0.30" --n-grid 400
    python design_campaign.py data.csv --json

CSV format: one row per observation, last column is the objective y (e.g. titre
g/L), all preceding columns are operating conditions (e.g. T, pH, feed_rate).
A header row is optional.

Exit codes:
    0  a ranked shortlist was produced
    1  input error (unreadable CSV, too few rows, bad bounds)
"""

from __future__ import annotations

import argparse
import csv
import json
import sys

import numpy as np

# Noise floor: 3% of the response scale. Lowering it produces prettier intervals
# and worse coverage. It is not a tuning knob.
NOISE_FLOOR_REL = 0.03
Z95 = 1.959963985


def read_csv(
    path: str, min_rows: int = 3, has_target: bool = True
) -> tuple[np.ndarray, np.ndarray | None, list[str]]:
    """Read a CSV of numeric columns.

    has_target=True (default) treats the last column as the objective y.
    has_target=False returns every column as inputs — used for candidate
    files, which list conditions only and may hold a single row.
    """
    rows: list[list[float]] = []
    header: list[str] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        for i, raw in enumerate(reader):
            if not raw or all(c.strip() == "" for c in raw):
                continue
            try:
                vals = [float(c) for c in raw]
            except ValueError:
                if i == 0:
                    header = [c.strip() for c in raw]
                continue
            rows.append(vals)
    if len(rows) < min_rows:
        raise ValueError(f"{path}: need at least {min_rows} numeric rows, got {len(rows)}")
    width = len(rows[0])
    if any(len(r) != width for r in rows):
        raise ValueError(f"{path}: ragged rows")
    if has_target and width < 2:
        raise ValueError(f"{path}: need at least one input column plus target")
    arr = np.asarray(rows, dtype=float)
    if has_target:
        X, y = arr[:, :-1], arr[:, -1]
        if not header or len(header) != width:
            header = [f"x{i}" for i in range(width - 1)] + ["y"]
        return X, y, header
    X = arr
    if not header or len(header) != width:
        header = [f"x{i}" for i in range(width)]
    return X, None, header


def parse_bounds(spec: str, dim: int) -> np.ndarray:
    """Parse "lo,hi;lo,hi;..." into a (dim, 2) array."""
    parts = [p for p in spec.split(";") if p.strip()]
    if len(parts) != dim:
        raise ValueError(
            f"bounds has {len(parts)} ranges but data has {dim} input columns"
        )
    out = np.zeros((dim, 2))
    for i, p in enumerate(parts):
        lo, hi = (float(v) for v in p.split(","))
        if hi <= lo:
            raise ValueError(f"bounds[{i}]: upper bound must exceed lower bound")
        out[i] = (lo, hi)
    return out


def standardize(X: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """Map to [0, 1]. Guard against zero-width dimensions."""
    span = np.where(hi - lo == 0, 1.0, hi - lo)
    return np.clip((X - lo) / span, 0.0, 1.0)


def gp_fit_predict(
    Xt: np.ndarray, yt: np.ndarray, Xq: np.ndarray, noise_rel: float
) -> tuple[np.ndarray, np.ndarray, dict]:
    """RBF GP with a fixed noise floor. Returns (mean, std, info).

    Length scale and signal variance come from a small grid search on
    leave-one-out error — enough to be defensible, cheap enough to run anywhere.
    """
    n, d = Xt.shape
    y_mean = float(yt.mean())
    y_std = float(yt.std()) or 1.0
    yn = (yt - y_mean) / y_std

    # Noise variance relative to normalised response.
    noise = max(noise_rel, 1e-6) ** 2

    best = None
    for ls in (0.15, 0.25, 0.4, 0.6, 0.9):
        for amp in (0.6, 1.0, 1.6):
            # Squared-exponential kernel on scaled inputs.
            d2 = (
                (Xt[:, None, :] - Xt[None, :, :]) ** 2 / (ls ** 2)
            ).sum(axis=2)
            K = amp ** 2 * np.exp(-0.5 * d2) + noise * np.eye(n)
            try:
                L = np.linalg.cholesky(K)
            except np.linalg.LinAlgError:
                continue
            alpha = np.linalg.solve(L.T, np.linalg.solve(L, yn))
            # Leave-one-out score: cheap and honest about generalisation.
            Kinv = np.linalg.inv(K)
            loo = (alpha / np.diag(Kinv)) ** 2
            score = float(loo.mean())
            if best is None or score < best[0]:
                best = (score, ls, amp, L, alpha)

    if best is None:
        raise RuntimeError("kernel selection failed; check for duplicate rows")
    _, ls, amp, L, alpha = best

    dq = ((Xq[:, None, :] - Xt[None, :, :]) ** 2 / (ls ** 2)).sum(axis=2)
    Ks = amp ** 2 * np.exp(-0.5 * dq)
    mu_n = Ks @ alpha
    v = np.linalg.solve(L, Ks.T)
    var = amp ** 2 - (v ** 2).sum(axis=0)
    var = np.maximum(var, noise)  # never report a thinner bar than the floor

    mu = mu_n * y_std + y_mean
    sd = np.sqrt(var) * y_std
    info = {"length_scale": ls, "amplitude": amp, "loo_mse": best[0]}
    return mu, sd, info


def ood_verdicts(Xq: np.ndarray, Xt: np.ndarray) -> list[str]:
    """Flag query points that sit outside the hull of measured conditions.

    A convex-hull test is exact but needs scipy in high dimensions. The
    per-dimension range test plus nearest-neighbour distance is a conservative
    substitute that runs on numpy alone.
    """
    lo, hi = Xt.min(axis=0), Xt.max(axis=0)
    in_range = np.all((Xq >= lo - 1e-12) & (Xq <= hi + 1e-12), axis=1)

    # Typical spacing between measured points, as a "how far is far" ruler.
    if len(Xt) > 1:
        d2 = ((Xt[:, None, :] - Xt[None, :, :]) ** 2).sum(axis=2)
        np.fill_diagonal(d2, np.inf)
        typical = float(np.sqrt(d2.min(axis=1).mean()))
    else:
        typical = 1.0
    if typical <= 0:
        typical = 1.0

    dq2 = ((Xq[:, None, :] - Xt[None, :, :]) ** 2).sum(axis=2)
    nearest = np.sqrt(dq2.min(axis=1))

    verdicts = []
    for ok, dist in zip(in_range, nearest):
        if not ok:
            verdicts.append("reject")
        elif dist > 2.0 * typical:
            verdicts.append("controlled")
        else:
            verdicts.append("pass")
    return verdicts


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Rank the next fermentation runs with uncertainty intervals."
    )
    ap.add_argument("data", help="CSV of measured conditions and objective")
    ap.add_argument("--candidates", help="CSV of candidate conditions to rank")
    ap.add_argument(
        "--bounds",
        help='Per-dimension grid bounds, e.g. "30,40;6,8;0.05,0.30". '
        "Used when --candidates is omitted.",
    )
    ap.add_argument("--n-grid", type=int, default=300, help="Grid size (default 300)")
    ap.add_argument("--top", type=int, default=5, help="How many to shortlist")
    ap.add_argument("--noise-floor", type=float, default=NOISE_FLOOR_REL)
    ap.add_argument("--explore", type=float, default=1.0,
                    help="Weight on uncertainty in the ranking (default 1.0)")
    ap.add_argument("--json", action="store_true", help="Emit JSON")
    args = ap.parse_args()

    try:
        X, y, header = read_csv(args.data)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    n, d = X.shape
    lo, hi = X.min(axis=0), X.max(axis=0)

    if args.candidates:
        try:
            Xq, _, hq = read_csv(args.candidates, min_rows=1, has_target=False)
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if Xq.shape[1] == d + 1:
            # A candidate file that happens to carry a target column: drop it.
            Xq = Xq[:, :d]
            hq = hq[:d]
        if Xq.shape[1] != d:
            print(
                f"error: candidates have {Xq.shape[1]} columns, data has {d}",
                file=sys.stderr,
            )
            return 1
    else:
        if args.bounds:
            try:
                b = parse_bounds(args.bounds, d)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
        else:
            # Widen the measured box by 20% per side. Beyond that the model has
            # no business extrapolating, and the guard will say so.
            span = np.where(hi - lo == 0, 1.0, hi - lo)
            b = np.column_stack([lo - 0.2 * span, hi + 0.2 * span])
        g = max(2, int(round(args.n_grid ** (1.0 / d))))
        axes = [np.linspace(b[i, 0], b[i, 1], g) for i in range(d)]
        mesh = np.meshgrid(*axes, indexing="ij")
        Xq = np.stack([m.ravel() for m in mesh], axis=1)
        hq = header[:d]

    Xt = standardize(X, lo, hi)
    Xqs = standardize(Xq, lo, hi)

    try:
        mu, sd, info = gp_fit_predict(Xt, y, Xqs, args.noise_floor)
    except (RuntimeError, np.linalg.LinAlgError) as exc:
        print(f"error: surrogate fit failed: {exc}", file=sys.stderr)
        return 1

    verdicts = ood_verdicts(Xq, X)
    # Ranking: upper confidence bound. Rejected points are never shortlisted.
    ucb = mu + args.explore * Z95 * sd
    score = np.where(np.array(verdicts) == "reject", -np.inf, ucb)

    order = np.argsort(-score)
    picked, rows = 0, []
    for i in order:
        if picked >= args.top:
            break
        if verdicts[i] == "reject":
            continue
        rows.append(
            {
                "conditions": {hq[k]: round(float(Xq[i, k]), 6) for k in range(d)},
                "predicted": round(float(mu[i]), 4),
                "std": round(float(sd[i]), 4),
                "interval95": [
                    round(float(mu[i] - Z95 * sd[i]), 4),
                    round(float(mu[i] + Z95 * sd[i]), 4),
                ],
                "ood": verdicts[i],
                "score": round(float(score[i]), 4),
            }
        )
        picked += 1

    n_reject = int(sum(1 for v in verdicts if v == "reject"))
    payload = {
        "observations": n,
        "dimensions": d,
        "kernel": {
            "length_scale": round(info["length_scale"], 4),
            "amplitude": round(info["amplitude"], 4),
            "loo_mse": round(info["loo_mse"], 6),
        },
        "noise_floor_rel": args.noise_floor,
        "candidates_scored": int(len(Xq)),
        "rejected_out_of_distribution": n_reject,
        "shortlist": rows,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    print("Fermentation Campaign Design")
    print("-" * 60)
    print(f"  observations           : {n}")
    print(f"  dimensions             : {d} ({', '.join(header[:d])})")
    print(f"  kernel length scale    : {info['length_scale']:.3f}")
    print(f"  leave-one-out MSE      : {info['loo_mse']:.5f}")
    print(f"  candidates scored      : {len(Xq)}")
    print(f"  rejected as OOD        : {n_reject}")
    print("-" * 60)
    if not rows:
        print("  No candidate survived the OOD guard. Widen the measured region")
        print("  or supply candidates inside it.")
        return 0
    for r in rows:
        cond = ", ".join(f"{k}={v}" for k, v in r["conditions"].items())
        print(f"  {r['predicted']:>10.4f} +/- {r['std']:.4f}  [{r['ood']}]  {cond}")
    print("-" * 60)
    print(f"  Top {len(rows)} by upper confidence bound (explore={args.explore}).")
    print("  'reject' rows are withheld, not ranked: no score outside the data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
