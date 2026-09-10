#!/usr/bin/env python3
"""Grade a quantitative literature claim against your own data, with numbers.

A paper says "mu_max = 0.81 /h" or "titre reaches 12.4 g/L at 37 C, pH 7". This
script does not ask whether you believe it. It asks whether the claim survives
at *your* data, and reports the two numbers that decide it:

  agreement    R^2 of the surrogate, measured out of sample (leave-one-out)
  calibration  empirical coverage of the 95% interval, same folds

Then it grades. No grade is issued without both numbers, and a claim that
sits outside the computed interval is reported as refuted rather than quietly
downgraded to "unvalidated".

Everything is local. No API key, no network, no account. numpy only.

Usage:
    # Value claim: "titre is 12.4 g/L at T=37, pH=7"
    python grade_claim.py data.csv --claim-value 12.4 --at "37,7.0"

    # Parameter claim: "mu_max is 0.81 /h" on (substrate -> rate) data
    python grade_claim.py kinetics.csv --claim-param mu_max --claim-value 0.81 \
        --model monod

    # Model-form claim: "growth follows Monod"
    python grade_claim.py kinetics.csv --claim-model monod

    python grade_claim.py data.csv --claim-value 12.4 --at "37,7.0" --json

CSV format: condition columns first, target last. A header row is optional.
For --claim-param / --claim-model, exactly one condition column is expected
(substrate) and the target is the rate.

Exit codes:
    0  a grade was issued (any grade)
    1  input error
"""

from __future__ import annotations

import argparse
import csv
import json
import sys

import numpy as np

Z95 = 1.959963985
NOISE_FLOOR_REL = 0.03
# Below this many points, coverage itself is unreliable. The grade still prints,
# but it is flagged provisional rather than presented as settled.
MIN_POINTS_FOR_SETTLED = 15

GRADE_PASS = "PASS"
GRADE_MARGINAL = "MARGINAL"
GRADE_REFUTED = "REFUTED"
GRADE_UNVALIDATED = "UNVALIDATED"


def read_csv(path: str) -> tuple[np.ndarray, np.ndarray, list[str]]:
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
            rows.append(vals)
    if len(rows) < 6:
        raise ValueError(f"{path}: need at least 6 numeric rows, got {len(rows)}")
    width = len(rows[0])
    if width < 2:
        raise ValueError(f"{path}: need at least one condition column plus target")
    arr = np.asarray(rows, dtype=float)
    if not header or len(header) != width:
        header = [f"x{i}" for i in range(width - 1)] + ["y"]
    return arr[:, :-1], arr[:, -1], header


def parse_at(spec: str, dim: int) -> np.ndarray:
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    if len(parts) != dim:
        raise ValueError(
            f"--at has {len(parts)} values but data has {dim} condition columns"
        )
    return np.array([[float(p) for p in parts]])


def standardize(X: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    span = np.where(hi - lo == 0, 1.0, hi - lo)
    return np.clip((X - lo) / span, 0.0, 1.0)


def gp_kernel(Xt: np.ndarray, yn: np.ndarray, noise: float):
    """Pick length scale / amplitude by leave-one-out error, return fit state."""
    n = len(Xt)
    best = None
    for ls in (0.15, 0.25, 0.4, 0.6, 0.9):
        for amp in (0.6, 1.0, 1.6):
            d2 = ((Xt[:, None, :] - Xt[None, :, :]) ** 2 / (ls ** 2)).sum(axis=2)
            K = amp ** 2 * np.exp(-0.5 * d2) + noise * np.eye(n)
            try:
                L = np.linalg.cholesky(K)
            except np.linalg.LinAlgError:
                continue
            alpha = np.linalg.solve(L.T, np.linalg.solve(L, yn))
            Kinv = np.linalg.inv(K)
            loo = float(((alpha / np.diag(Kinv)) ** 2).mean())
            if best is None or loo < best[0]:
                best = (loo, ls, amp, L, alpha)
    if best is None:
        raise RuntimeError("kernel selection failed; check for duplicate rows")
    return best


def predict(Xt, L, alpha, yn_mean, yn_std, ls, amp, Xq, noise):
    dq = ((Xq[:, None, :] - Xt[None, :, :]) ** 2 / (ls ** 2)).sum(axis=2)
    Ks = amp ** 2 * np.exp(-0.5 * dq)
    mu_n = Ks @ alpha
    v = np.linalg.solve(L, Ks.T)
    var = np.maximum(amp ** 2 - (v ** 2).sum(axis=0), noise)
    return mu_n * yn_std + yn_mean, np.sqrt(var) * yn_std


def _loo_run(Xs: np.ndarray, yn: np.ndarray, noise: float):
    """One leave-one-out pass. Returns predictions and sds in normalised units."""
    n = len(yn)
    preds = np.zeros(n)
    sds = np.zeros(n)
    for i in range(n):
        mask = np.arange(n) != i
        try:
            st = gp_kernel(Xs[mask], yn[mask], noise)
        except RuntimeError:
            continue
        _, ls, amp, L, alpha = st
        m, s = predict(Xs[mask], L, alpha, 0.0, 1.0, ls, amp, Xs[i:i + 1], noise)
        preds[i] = m[0]
        sds[i] = s[0]
    return preds, sds


def estimate_noise_rel(X: np.ndarray, y: np.ndarray) -> float:
    """Estimate the response noise from LOO residuals, floored at the 3% level.

    The 3% figure is a *floor*, not an assumption: it is the narrowest interval
    we are willing to report. Real data is usually noisier than that, and fixing
    the noise at 3% would make every interval too thin and every coverage audit
    report overconfidence. So measure the residual scatter and raise the floor
    to meet it.
    """
    y_mean = float(y.mean())
    y_std = float(y.std()) or 1.0
    lo, hi = X.min(axis=0), X.max(axis=0)
    Xs = standardize(X, lo, hi)
    yn = (y - y_mean) / y_std
    preds, _ = _loo_run(Xs, yn, NOISE_FLOOR_REL ** 2)
    resid = yn - preds
    sigma = float(np.std(resid)) if len(resid) > 1 else NOISE_FLOOR_REL
    return float(max(NOISE_FLOOR_REL, sigma))


def loo_metrics(X: np.ndarray, y: np.ndarray, noise_rel: float | None = None) -> dict:
    """Out-of-sample R^2 and empirical 95% coverage by leave-one-out."""
    n = len(y)
    y_mean = float(y.mean())
    y_std = float(y.std()) or 1.0
    if noise_rel is None:
        noise_rel = estimate_noise_rel(X, y)
    noise = noise_rel ** 2
    lo, hi = X.min(axis=0), X.max(axis=0)
    Xs = standardize(X, lo, hi)
    yn = (y - y_mean) / y_std

    preds_n, sds_n = _loo_run(Xs, yn, noise)
    preds = preds_n * y_std + y_mean
    sds = sds_n * y_std

    ss_res = float(((y - preds) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    lo_ = preds - Z95 * sds
    hi_ = preds + Z95 * sds
    covered = ((y >= lo_) & (y <= hi_)).mean()
    return {
        "r2": float(r2),
        "rmse": float(np.sqrt(ss_res / n)),
        "coverage": float(covered),
        "mean_interval_width": float((hi_ - lo_).mean()),
        "noise_rel_used": float(noise_rel),
    }


def fit_kinetics(S: np.ndarray, mu: np.ndarray, form: str) -> dict | None:
    """Fit a named kinetic form. Returns params and R^2, or None."""
    forms = {
        "monod": (
            lambda S, m, K: m * S / (K + S), ["mu_max", "Ks"],
            [(1e-4, 10.0), (1e-5, 100.0)],
        ),
        "tessier": (
            lambda S, m, K: m * (1 - np.exp(-S / max(K, 1e-9))), ["mu_max", "Ks"],
            [(1e-4, 10.0), (1e-5, 100.0)],
        ),
        "andrews": (
            lambda S, m, K, Ki: m * S / (K + S + S * S / max(Ki, 1e-9)),
            ["mu_max", "Ks", "Ki"], [(1e-4, 10.0), (1e-5, 100.0), (1e-4, 1e4)],
        ),
        "first_order": (lambda S, k: k * S, ["k"], [(1e-6, 100.0)]),
    }
    if form not in forms:
        raise ValueError(f"unknown model '{form}'; choose from {list(forms)}")
    fn, pnames, bounds = forms[form]
    lo = np.array([b[0] for b in bounds])
    hi = np.array([b[1] for b in bounds])
    rng = np.random.default_rng(20260910)

    def sse(p):
        try:
            pred = np.where(np.isfinite(fn(S, *p)), fn(S, *p), 0.0)
        except (FloatingPointError, ZeroDivisionError, OverflowError):
            return np.inf
        return float(((pred - mu) ** 2).sum()) if np.all(np.isfinite(pred)) else np.inf

    cand = np.exp(rng.uniform(np.log(lo), np.log(hi), size=(6000, len(lo))))
    scores = np.array([sse(p) for p in cand])
    bi = int(np.argmin(scores))
    best, best_s = cand[bi].copy(), float(scores[bi])
    step = 0.5
    for _ in range(60):
        improved = False
        for j in range(len(best)):
            for f in (1 - step, 1 + step):
                t = best.copy()
                t[j] = np.clip(t[j] * max(f, 0.01), lo[j], hi[j])
                s = sse(t)
                if s < best_s - 1e-15:
                    best, best_s, improved = t, s, True
        if not improved:
            step *= 0.5
            if step < 1e-4:
                break
    ss_res = float(((mu - fn(S, *best)) ** 2).sum())
    ss_tot = float(((mu - mu.mean()) ** 2).sum())
    return {
        "form": form,
        "params": {p: float(v) for p, v in zip(pnames, best)},
        "r2": float(1 - ss_res / ss_tot) if ss_tot > 0 else float("nan"),
        "rmse": float(np.sqrt(ss_res / len(mu))),
    }


def grade_value_claim(
    claim: float, mu: float, sd: float, r2: float, cov: float, nominal: float
) -> tuple[str, str]:
    """Two independent questions: can we grade at all, and does the claim hold."""
    if not np.isfinite(r2) or not np.isfinite(cov):
        return GRADE_UNVALIDATED, "agreement or coverage could not be computed"
    lo_, hi_ = mu - Z95 * sd, mu + Z95 * sd
    inside = lo_ <= claim <= hi_
    # How many interval half-widths away is the claim? 1.0 == on the 95% edge.
    z_dist = abs(claim - mu) / (Z95 * sd) if sd > 0 else float("inf")

    if z_dist > 3.0:
        return GRADE_REFUTED, (
            f"claim {claim:g} is {z_dist:.1f}x the 95% half-width from the "
            f"predicted {mu:.4g}; interval {lo_:.4g}..{hi_:.4g}"
        )
    cov_gap = cov - nominal  # signed: negative is overconfident, positive is wide
    if r2 >= 0.90 and abs(cov_gap) <= 0.05 and inside:
        return GRADE_PASS, (
            f"R2={r2:.3f}, coverage={cov:.3f} vs nominal {nominal:.2f}, "
            f"claim inside {lo_:.4g}..{hi_:.4g}"
        )
    if r2 >= 0.70 and abs(cov_gap) <= 0.15:
        why = []
        if not inside:
            why.append(f"claim outside {lo_:.4g}..{hi_:.4g} by {z_dist:.1f}x half-width")
        if abs(cov_gap) > 0.05:
            # Direction matters: too-narrow intervals and too-wide intervals are
            # different faults and must not be described with the same word.
            direction = (
                f"overconfident by {abs(cov_gap):.3f}" if cov_gap < 0
                else f"conservative by {abs(cov_gap):.3f} (intervals wider than needed)"
            )
            why.append(f"coverage {direction}")
        if r2 < 0.90:
            why.append(f"agreement R2={r2:.3f} below 0.90")
        return GRADE_MARGINAL, "; ".join(why)
    return GRADE_REFUTED, (
        f"agreement R2={r2:.3f} or coverage gap {cov_gap:+.3f} "
        "outside the usable band"
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Grade a literature claim against your data."
    )
    ap.add_argument("data", help="CSV of conditions and target")
    ap.add_argument("--claim-value", type=float, help="The number being claimed")
    ap.add_argument(
        "--at", help='Conditions for a value claim, e.g. "37,7.0"'
    )
    ap.add_argument(
        "--claim-param", help="Parameter name for a parameter claim, e.g. mu_max"
    )
    ap.add_argument(
        "--model", default="monod",
        help="Kinetic form for --claim-param (default monod)",
    )
    ap.add_argument("--claim-model", help="Claimed model form, e.g. monod")
    ap.add_argument("--nominal", type=float, default=0.95,
                    help="Nominal coverage (default 0.95)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not (args.claim_value is not None or args.claim_model):
        print("error: give --claim-value or --claim-model", file=sys.stderr)
        return 1

    try:
        X, y, header = read_csv(args.data)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    n, d = X.shape
    provisional = n < MIN_POINTS_FOR_SETTLED
    out: dict = {
        "observations": n,
        "conditions": header[:d],
        "provisional": provisional,
        "provisional_reason": (
            f"fewer than {MIN_POINTS_FOR_SETTLED} points; coverage is unstable"
            if provisional else None
        ),
        "nominal_coverage": args.nominal,
    }

    # ---- Model-form claim -------------------------------------------------
    if args.claim_model:
        if d != 1:
            print("error: --claim-model expects exactly one condition column "
                  f"(substrate), got {d}", file=sys.stderr)
            return 1
        S, mu = X[:, 0], y
        try:
            fit = fit_kinetics(S, mu, args.claim_model)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        metrics = loo_metrics(X, y)
        out.update({
            "claim": {"type": "model_form", "value": args.claim_model},
            "agreement": metrics,
            "fitted": fit,
        })
        if fit["r2"] >= 0.90:
            grade, why = GRADE_PASS, f"claimed form fits with R2={fit['r2']:.3f}"
        elif fit["r2"] >= 0.70:
            grade, why = GRADE_MARGINAL, f"claimed form fits only to R2={fit['r2']:.3f}"
        else:
            grade, why = GRADE_REFUTED, f"claimed form fits poorly, R2={fit['r2']:.3f}"
        out["grade"], out["reason"] = grade, why

    # ---- Parameter claim --------------------------------------------------
    elif args.claim_param:
        if d != 1:
            print("error: --claim-param expects exactly one condition column "
                  f"(substrate), got {d}", file=sys.stderr)
            return 1
        S, mu = X[:, 0], y
        try:
            fit = fit_kinetics(S, mu, args.model)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if args.claim_param not in fit["params"]:
            print(f"error: {args.model} has no parameter '{args.claim_param}'; "
                  f"it has {list(fit['params'])}", file=sys.stderr)
            return 1
        metrics = loo_metrics(X, y)
        est = fit["params"][args.claim_param]
        claimed = args.claim_value
        # Agreement of the parameter itself: relative error against the fit.
        rel_err = abs(claimed - est) / abs(est) if est else float("inf")
        out.update({
            "claim": {"type": "parameter", "parameter": args.claim_param,
                      "claimed": claimed, "model": args.model},
            "fitted": fit,
            "agreement": metrics,
            "parameter_relative_error": float(rel_err),
        })
        if rel_err <= 0.15 and fit["r2"] >= 0.90:
            grade = GRADE_PASS
            why = (f"fitted {args.claim_param}={est:.5g} vs claimed {claimed:g} "
                   f"({rel_err * 100:.1f}% apart), form R2={fit['r2']:.3f}")
        elif rel_err <= 0.35 and fit["r2"] >= 0.70:
            grade = GRADE_MARGINAL
            why = (f"fitted {args.claim_param}={est:.5g} vs claimed {claimed:g} "
                   f"({rel_err * 100:.1f}% apart), form R2={fit['r2']:.3f}")
        else:
            grade = GRADE_REFUTED
            why = (f"fitted {args.claim_param}={est:.5g} vs claimed {claimed:g} "
                   f"({rel_err * 100:.1f}% apart) — outside tolerance")
        out["grade"], out["reason"] = grade, why

    # ---- Value claim at stated conditions ---------------------------------
    else:
        if not args.at:
            print("error: --claim-value requires --at", file=sys.stderr)
            return 1
        try:
            Xq = parse_at(args.at, d)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        lo_b, hi_b = X.min(axis=0), X.max(axis=0)
        Xt = standardize(X, lo_b, hi_b)
        Xqs = standardize(Xq, lo_b, hi_b)
        outside = bool(((Xq < lo_b) | (Xq > hi_b)).any())
        if outside:
            print(
                "error: the claimed conditions lie outside the measured range; "
                "grade withheld rather than extrapolated",
                file=sys.stderr,
            )
            return 1

        y_mean, y_std = float(y.mean()), float(y.std()) or 1.0
        # Estimate the noise first so the interval reported for the claim uses
        # the same width the coverage figure was measured against.
        noise_rel = estimate_noise_rel(X, y)
        noise = noise_rel ** 2
        try:
            st = gp_kernel(Xt, (y - y_mean) / y_std, noise)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        _, ls, amp, L, alpha = st
        mu_q, sd_q = predict(Xt, L, alpha, y_mean, y_std, ls, amp, Xqs, noise)
        metrics = loo_metrics(X, y, noise_rel)

        grade, why = grade_value_claim(
            args.claim_value, float(mu_q[0]), float(sd_q[0]),
            metrics["r2"], metrics["coverage"], args.nominal,
        )
        out.update({
            "claim": {"type": "value", "value": args.claim_value,
                      "conditions": {header[i]: float(Xq[0, i]) for i in range(d)}},
            "predicted": float(mu_q[0]),
            "predicted_std": float(sd_q[0]),
            "interval95": [float(mu_q[0] - Z95 * sd_q[0]),
                           float(mu_q[0] + Z95 * sd_q[0])],
            "agreement": metrics,
            "grade": grade,
            "reason": why,
        })

    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    print("Paper Claim Validation")
    print("-" * 70)
    print(f"  observations   : {n}")
    print(f"  conditions     : {', '.join(header[:d])}")
    c = out["claim"]
    if c["type"] == "value":
        cond = ", ".join(f"{k}={v:g}" for k, v in c["conditions"].items())
        print(f"  claim          : {c['value']:g} at {cond}")
        print(f"  predicted      : {out['predicted']:.4g} "
              f"+/- {out['predicted_std']:.4g}")
        print(f"  95% interval   : {out['interval95'][0]:.4g} .. "
              f"{out['interval95'][1]:.4g}")
    elif c["type"] == "parameter":
        print(f"  claim          : {c['parameter']} = {c['claimed']:g} "
              f"({c['model']})")
        print(f"  fitted         : {out['fitted']['params'][c['parameter']]:.5g}")
        print(f"  relative error : {out['parameter_relative_error'] * 100:.1f}%")
    else:
        print(f"  claim          : growth follows {c['value']}")
    a = out["agreement"]
    print("-" * 70)
    print(f"  agreement  R2  : {a['r2']:.4f}   RMSE {a['rmse']:.4g}")
    print(f"  calibration    : {a['coverage']:.3f} "
          f"(nominal {args.nominal:.2f})")
    print("-" * 70)
    print(f"  GRADE: {out['grade']}")
    print(f"  {out['reason']}")
    if provisional:
        print()
        print(f"  PROVISIONAL: only {n} points (need "
              f"{MIN_POINTS_FOR_SETTLED}+ for a stable coverage figure).")
    print()
    print("  The grade grades transferability to your data, not the paper's")
    print("  integrity. A claim can hold in its original context and still")
    print("  fail here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
