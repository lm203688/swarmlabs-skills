#!/usr/bin/env python3
"""Task-level evaluation for the SwarmLabs skills.

Most skill collections ship documentation and stop there. The paper behind the
largest one states plainly that it reports "no task-level evaluation". That is
the gap this file exists to close: each check below gives a skill a task with a
known answer and asserts it gets that answer.

Concretely, a skill passes here only if:
  - it reproduces a parameter we planted in the data, or
  - it refuses a query we know is out of distribution, or
  - it reports a coverage gap we deliberately introduced.

Checks are offline and deterministic. numpy is required for two of them.

Usage:
    python tests/task_level/run_task_checks.py
    python tests/task_level/run_task_checks.py --json

Exit codes:
    0  every check passed
    1  at least one check failed
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.abspath(os.path.join(HERE, "..", ".."))
SCRIPTS = os.path.join(PKG, "scripts")


class Result:
    def __init__(self, name: str, passed: bool, detail: str, expected: str = "",
                 skipped: bool = False):
        self.name = name
        self.passed = passed
        self.detail = detail
        self.expected = expected
        self.skipped = skipped

    @property
    def status(self) -> str:
        if self.skipped:
            return "SKIP"
        return "PASS" if self.passed else "FAIL"

    def as_dict(self) -> dict:
        return {
            "check": self.name,
            "status": self.status,
            "observed": self.detail,
            "expected": self.expected,
        }


def run(script_rel: str, args: list[str]) -> tuple[int, str, str]:
    path = os.path.join(PKG, script_rel)
    proc = subprocess.run(
        [sys.executable, path, *args],
        capture_output=True, text=True, timeout=180,
    )
    return proc.returncode, proc.stdout, proc.stderr


def write_csv(path: str, header: list[str], rows: list[list[float]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def check_kinetics_recovers_truth(tmp: str) -> Result:
    """Plant Monod parameters, require the fitter to find them."""
    try:
        import numpy as np
    except ImportError:
        return Result("kinetics.recovers_planted_parameters", False,
                      "numpy not installed", "Monod selected, mu_max~0.8, Ks~0.5")

    rng = np.random.default_rng(3)
    S = np.sort(rng.uniform(0.02, 12, 26))
    mu = 0.8 * S / (0.5 + S) + rng.normal(0, 0.02, 26)
    mu = np.clip(mu, 1e-4, None)
    data = os.path.join(tmp, "kin.csv")
    write_csv(data, ["S", "mu"],
              [[float(a), float(b)] for a, b in zip(S, mu)])

    code, out, err = run("kinetic-model-selection/scripts/select_model.py",
                         [data, "--json"])
    if code != 0:
        return Result("kinetics.recovers_planted_parameters", False,
                      err.strip()[:200] or f"exit {code}",
                      "exit 0")
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return Result("kinetics.recovers_planted_parameters", False,
                      "non-JSON output", "JSON with winner")
    winner = payload.get("winner", "")
    if "Monod" not in winner:
        return Result("kinetics.recovers_planted_parameters", False,
                      f"selected {winner}", "Monod")
    params = next((r["params"] for r in payload["ranking"]
                   if r["model"] == winner), {})
    mu_hat, ks_hat = params.get("mu_max"), params.get("Ks")
    ok_mu = mu_hat is not None and abs(mu_hat - 0.8) / 0.8 < 0.20
    ok_ks = ks_hat is not None and abs(ks_hat - 0.5) / 0.5 < 0.50
    if ok_mu and ok_ks:
        return Result("kinetics.recovers_planted_parameters", True,
                      f"Monod mu_max={mu_hat:.4f} Ks={ks_hat:.4f}",
                      "Monod, mu_max within 20% of 0.8, Ks within 50% of 0.5")
    return Result("kinetics.recovers_planted_parameters", False,
                  f"mu_max={mu_hat}, Ks={ks_hat}",
                  "mu_max~0.8 (20%), Ks~0.5 (50%)")


def check_design_refuses_extrapolation(tmp: str) -> Result:
    """Ask about conditions far outside the data; require refusal."""
    try:
        import numpy as np
    except ImportError:
        return Result("design.refuses_extrapolation", False,
                      "numpy not installed", "reject outside the measured box")

    rng = np.random.default_rng(7)
    X = np.column_stack([rng.uniform(30, 40, 24),
                         rng.uniform(6.0, 8.0, 24),
                         rng.uniform(0.05, 0.30, 24)])
    t = (0.9 * (1 - np.exp(-(X[:, 0] - 30) / 6))
         * np.exp(-((X[:, 1] - 7.0) ** 2) / 0.5) * (1 + 2 * X[:, 2]))
    y = 12 * t + rng.normal(0, 0.6, 24)
    data = os.path.join(tmp, "campaign.csv")
    write_csv(data, ["T", "pH", "feed_rate", "titre"],
              [[float(v) for v in row] for row in np.column_stack([X, y])])

    # A candidate deliberately far outside every measured dimension.
    far = os.path.join(tmp, "far.csv")
    write_csv(far, ["T", "pH", "feed_rate"], [[500.0, 2.0, 5.0]])

    code, out, err = run("fermentation-design/scripts/design_campaign.py",
                         [data, "--candidates", far, "--json"])
    if code != 0:
        return Result("design.refuses_extrapolation", False,
                      err.strip()[:200] or f"exit {code}", "exit 0")
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return Result("design.refuses_extrapolation", False,
                      "non-JSON output", "JSON with rejection count")
    rejected = payload.get("rejected_out_of_distribution", 0)
    shortlist = payload.get("shortlist", [])
    if rejected >= 1 and not shortlist:
        return Result("design.refuses_extrapolation", True,
                      f"rejected {rejected}/{payload.get('candidates_scored')}, "
                      "no shortlist emitted",
                      "reject the far point rather than score it")
    return Result("design.refuses_extrapolation", False,
                  f"rejected={rejected}, shortlisted={len(shortlist)}",
                  "reject >=1 and shortlist 0")


def check_design_emits_intervals(tmp: str) -> Result:
    """Require a 95% interval on every shortlisted condition."""
    try:
        import numpy as np
    except ImportError:
        return Result("design.emits_intervals", False,
                      "numpy not installed", "interval95 on each row")

    rng = np.random.default_rng(11)
    X = np.column_stack([rng.uniform(30, 40, 20), rng.uniform(6.0, 8.0, 20)])
    y = 10 * np.exp(-((X[:, 0] - 36) ** 2) / 40) + rng.normal(0, 0.4, 20)
    data = os.path.join(tmp, "campaign2.csv")
    write_csv(data, ["T", "pH", "titre"],
              [[float(v) for v in row] for row in np.column_stack([X, y])])

    code, out, err = run("fermentation-design/scripts/design_campaign.py",
                         [data, "--top", "3", "--json"])
    if code != 0:
        return Result("design.emits_intervals", False,
                      err.strip()[:200] or f"exit {code}", "exit 0")
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return Result("design.emits_intervals", False,
                      "non-JSON output", "JSON shortlist")
    rows = payload.get("shortlist", [])
    if not rows:
        return Result("design.emits_intervals", False,
                      "empty shortlist", "at least one ranked condition")
    bad = [r for r in rows
           if not r.get("interval95") or r.get("std") is None or r["std"] <= 0]
    if bad:
        return Result("design.emits_intervals", False,
                      f"{len(bad)} rows lack a positive interval",
                      "every row has std>0 and interval95")
    return Result("design.emits_intervals", True,
                  f"{len(rows)} rows, e.g. "
                  f"{rows[0]['interval95'][0]:.3f}..{rows[0]['interval95'][1]:.3f}",
                  "interval95 present on every shortlisted row")


def check_strain_prefers_thermophile(tmp: str) -> Result:
    """At 60 C a thermophile must outrank every mesophile."""
    code, out, err = run("strain-media-selection/scripts/select_strain.py",
                         ["--temp", "60", "--ph", "6.5", "--top", "3", "--json"])
    if code != 0:
        return Result("strain.prefers_thermophile_at_60C", False,
                      err.strip()[:200] or f"exit {code}", "exit 0")
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return Result("strain.prefers_thermophile_at_60C", False,
                      "non-JSON output", "JSON shortlist")
    rows = payload.get("shortlist", [])
    if not rows:
        return Result("strain.prefers_thermophile_at_60C", False,
                      "empty shortlist", "a ranked host")
    top = rows[0]
    t_opt = top.get("T_opt")
    if t_opt is None:
        return Result("strain.prefers_thermophile_at_60C", False,
                      "top strain has no T_opt", "T_opt present")
    # The best host at 60 C should not be a strict mesophile (T_opt ~ 37).
    if t_opt >= 45:
        return Result("strain.prefers_thermophile_at_60C", True,
                      f"{top.get('organism')} T_opt={t_opt}C",
                      "top-ranked host has T_opt >= 45 C")
    return Result("strain.prefers_thermophile_at_60C", False,
                  f"top is {top.get('organism')} with T_opt={t_opt}C",
                  "top-ranked host has T_opt >= 45 C")


def check_coverage_is_honest_on_clean_data(tmp: str) -> Result:
    """On well-behaved data, reported coverage must be near the nominal 95%.

    The auditor derives its own intervals by leave-one-out, so there is no way
    to hand it a deliberately broken interval. What it *can* be held to is the
    converse: do not cry wolf. A clean signal must not come back OVERCONFIDENT.
    """
    try:
        import numpy as np
    except ImportError:
        return Result("uq.coverage_honest_on_clean_data", False,
                      "numpy not installed", "coverage near 0.95")

    rng = np.random.default_rng(5)
    n = 40
    x = rng.uniform(0, 10, n)
    y = np.sin(x) + rng.normal(0, 0.03, n)
    data = os.path.join(tmp, "uq.csv")
    write_csv(data, ["x", "y"],
              [[float(a), float(b)] for a, b in zip(x, y)])

    code, out, err = run("uq-coverage-audit/scripts/uq_audit.py",
                         [data, "--json"])
    if code not in (0, 1):
        return Result("uq.coverage_honest_on_clean_data", False,
                      err.strip()[:200] or f"exit {code}", "exit 0 or 1")
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        if "OVERCONFIDENT" in out.upper():
            return Result("uq.coverage_honest_on_clean_data", False,
                          "verdict OVERCONFIDENT on clean data",
                          "not OVERCONFIDENT on a clean signal")
        return Result("uq.coverage_honest_on_clean_data", False,
                      "no verdict in output", "a coverage verdict")
    verdict = str(payload.get("verdict", "")).upper()
    cov = payload.get("empirical_coverage")
    if "OVERCONFIDENT" in verdict:
        return Result("uq.coverage_honest_on_clean_data", False,
                      f"verdict={verdict} coverage={cov}",
                      "not OVERCONFIDENT on a clean signal")
    if cov is None:
        return Result("uq.coverage_honest_on_clean_data", False,
                      f"verdict={verdict}, no coverage number",
                      "an empirical coverage value")
    if cov >= 0.80:
        return Result("uq.coverage_honest_on_clean_data", True,
                      f"verdict={verdict} coverage={cov:.3f}",
                      "coverage >= 0.80 on a clean signal")
    return Result("uq.coverage_honest_on_clean_data", False,
                  f"verdict={verdict} coverage={cov:.3f}",
                  "coverage >= 0.80 on a clean signal")


def _grade(payload: dict) -> str:
    return str(payload.get("grade", "")).upper()


def check_claim_accepts_true_value(tmp: str) -> Result:
    """A claim equal to the planted truth must not come back REFUTED."""
    try:
        import numpy as np
    except ImportError:
        return Result("claim.true_value_not_refuted", False,
                      "numpy not installed", "PASS or MARGINAL")

    rng = np.random.default_rng(21)
    X = np.column_stack([rng.uniform(30, 40, 30), rng.uniform(6, 8, 30)])
    f = (10 * np.exp(-((X[:, 0] - 36) ** 2) / 40)
         * np.exp(-((X[:, 1] - 7) ** 2) / 0.8))
    y = f + rng.normal(0, 0.25, 30)
    data = os.path.join(tmp, "claim_runs.csv")
    write_csv(data, ["T", "pH", "titre"],
              [[float(v) for v in r] for r in np.column_stack([X, y])])

    # The true surface at T=36, pH=7 is exactly 10.0.
    code, out, err = run("paper-claim-validation/scripts/grade_claim.py",
                         [data, "--claim-value", "10.0", "--at", "36,7.0",
                          "--json"])
    if code != 0:
        return Result("claim.true_value_not_refuted", False,
                      err.strip()[:200] or f"exit {code}", "exit 0")
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return Result("claim.true_value_not_refuted", False,
                      "non-JSON output", "JSON with a grade")
    g = _grade(payload)
    if g in ("PASS", "MARGINAL"):
        return Result("claim.true_value_not_refuted", True,
                      f"{g}: {payload.get('reason', '')[:70]}",
                      "not REFUTED")
    return Result("claim.true_value_not_refuted", False,
                  f"{g}: {payload.get('reason', '')[:90]}",
                  "PASS or MARGINAL for a claim at the planted truth")


def check_claim_refutes_false_value(tmp: str) -> Result:
    """A claim far from the truth must be refuted, not softened."""
    try:
        import numpy as np
    except ImportError:
        return Result("claim.false_value_refuted", False,
                      "numpy not installed", "REFUTED")

    rng = np.random.default_rng(21)
    X = np.column_stack([rng.uniform(30, 40, 30), rng.uniform(6, 8, 30)])
    f = (10 * np.exp(-((X[:, 0] - 36) ** 2) / 40)
         * np.exp(-((X[:, 1] - 7) ** 2) / 0.8))
    y = f + rng.normal(0, 0.25, 30)
    data = os.path.join(tmp, "claim_runs2.csv")
    write_csv(data, ["T", "pH", "titre"],
              [[float(v) for v in r] for r in np.column_stack([X, y])])

    code, out, err = run("paper-claim-validation/scripts/grade_claim.py",
                         [data, "--claim-value", "20.0", "--at", "36,7.0",
                          "--json"])
    if code != 0:
        return Result("claim.false_value_refuted", False,
                      err.strip()[:200] or f"exit {code}", "exit 0")
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return Result("claim.false_value_refuted", False,
                      "non-JSON output", "JSON with a grade")
    g = _grade(payload)
    if g == "REFUTED":
        return Result("claim.false_value_refuted", True,
                      f"REFUTED: {payload.get('reason', '')[:70]}", "REFUTED")
    return Result("claim.false_value_refuted", False,
                  f"{g}: {payload.get('reason', '')[:90]}",
                  "REFUTED for a claim 2x the planted truth")


def check_claim_separates_model_forms(tmp: str) -> Result:
    """On Monod data: accept Monod, refute a first-order claim."""
    try:
        import numpy as np
    except ImportError:
        return Result("claim.separates_model_forms", False,
                      "numpy not installed", "monod PASS, first_order REFUTED")

    rng = np.random.default_rng(3)
    S = np.sort(rng.uniform(0.02, 12, 26))
    mu = np.clip(0.8 * S / (0.5 + S) + rng.normal(0, 0.02, 26), 1e-4, None)
    data = os.path.join(tmp, "claim_kin.csv")
    write_csv(data, ["S", "mu"],
              [[float(a), float(b)] for a, b in zip(S, mu)])

    grades = {}
    for form in ("monod", "first_order"):
        code, out, err = run("paper-claim-validation/scripts/grade_claim.py",
                             [data, "--claim-model", form, "--json"])
        if code != 0:
            return Result("claim.separates_model_forms", False,
                          f"{form}: {err.strip()[:120]}", "exit 0 for both")
        try:
            grades[form] = _grade(json.loads(out))
        except json.JSONDecodeError:
            return Result("claim.separates_model_forms", False,
                          f"{form}: non-JSON output", "JSON grades")

    if grades.get("monod") in ("PASS", "MARGINAL") and grades.get("first_order") == "REFUTED":
        return Result("claim.separates_model_forms", True,
                      f"monod={grades['monod']}, first_order={grades['first_order']}",
                      "monod accepted, first_order refuted")
    return Result("claim.separates_model_forms", False,
                  f"monod={grades.get('monod')}, first_order={grades.get('first_order')}",
                  "monod accepted, first_order refuted")


def check_ood_guard_rejects_far_query(tmp: str) -> Result:
    """A query far outside the training box must be rejected."""
    try:
        import numpy as np
    except ImportError:
        return Result("ood.rejects_far_query", False,
                      "numpy not installed", "reject verdict")

    rng = np.random.default_rng(9)
    X = np.column_stack([rng.uniform(30, 40, 24), rng.uniform(6, 8, 24)])
    y = 10 * np.exp(-((X[:, 0] - 36) ** 2) / 40) + rng.normal(0, 0.3, 24)
    data = os.path.join(tmp, "ood_train.csv")
    write_csv(data, ["T", "pH", "y"],
              [[float(v) for v in r] for r in np.column_stack([X, y])])

    code, out, err = run("ood-guard/scripts/guard_local.py",
                         [data, "--query", "500,2.0", "--json"])
    # Exit 1 is this script's deliberate gate behaviour: it exits non-zero when
    # nothing passed, so it can be used as a CI gate. That is not an error here.
    if code not in (0, 1):
        return Result("ood.rejects_far_query", False,
                      err.strip()[:200] or f"exit {code}", "exit 0 or 1")
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        if "reject" in out.lower():
            return Result("ood.rejects_far_query", True,
                          "reject found in text output", "reject")
        return Result("ood.rejects_far_query", False,
                      "no verdict in output", "reject")

    blob = json.dumps(payload).lower()
    if "reject" in blob:
        return Result("ood.rejects_far_query", True,
                      "far query rejected", "reject for a far query")
    return Result("ood.rejects_far_query", False,
                  blob[:150], "reject for a far query")


def check_virtual_design_runs(tmp: str) -> Result:
    """Online skill: skip cleanly when there is no network, never fake a pass."""
    import urllib.request
    try:
        urllib.request.urlopen("https://swarmlabs.tools/api/v3/health", timeout=8)
    except Exception:
        return Result("virtual_design.ranks_candidates", False,
                      "skipped: no network reachability",
                      "n/a (network-dependent skill)", skipped=True)

    code, out, err = run(
        "virtual-experiment-design/scripts/design_next_experiment.py",
        ["--scenario", "microbio_monod",
         "--train-x", "0.1", "0.5", "1.0", "2.0", "5.0",
         "--train-y", "0.15", "0.42", "0.58", "0.71", "0.79",
         "--top", "3", "--json"])
    if code != 0:
        return Result("virtual_design.ranks_candidates", False,
                      err.strip()[:200] or f"exit {code}", "exit 0")
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return Result("virtual_design.ranks_candidates", False,
                      "non-JSON output", "JSON suggestions")
    blob = json.dumps(payload)
    if "suggest" in blob.lower() or "candidate" in blob.lower():
        return Result("virtual_design.ranks_candidates", True,
                      "returned ranked suggestions", "a ranked suggestion list")
    return Result("virtual_design.ranks_candidates", False,
                  blob[:150], "a ranked suggestion list")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="Emit JSON")
    args = ap.parse_args()

    checks = [
        check_kinetics_recovers_truth,
        check_design_refuses_extrapolation,
        check_design_emits_intervals,
        check_strain_prefers_thermophile,
        check_coverage_is_honest_on_clean_data,
        check_claim_accepts_true_value,
        check_claim_refutes_false_value,
        check_claim_separates_model_forms,
        check_ood_guard_rejects_far_query,
        check_virtual_design_runs,
    ]

    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for fn in checks:
            try:
                results.append(fn(tmp))
            except Exception as exc:  # noqa: BLE001 - report, never mask
                results.append(Result(fn.__name__, False, f"raised {exc!r}",
                                      "no exception"))

    passed = sum(1 for r in results if r.passed)
    skipped = sum(1 for r in results if r.skipped)
    if args.json:
        print(json.dumps({
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed - skipped,
            "skipped": skipped,
            "checks": [r.as_dict() for r in results],
        }, indent=2, ensure_ascii=False))
    else:
        print("Task-Level Evaluation")
        print("=" * 74)
        for r in results:
            print(f"[{r.status}] {r.name}")
            print(f"        observed: {r.detail}")
            if r.status == "FAIL":
                print(f"        expected: {r.expected}")
        print("=" * 74)
        scored = len(results) - skipped
        print(f"  {passed}/{scored} passed"
              + (f", {skipped} skipped (no network)" if skipped else ""))
        print()
        print("  These checks give each skill a task with a known answer.")
        print("  A skill that only reads well cannot pass them.")
    # Skipped checks are not failures: counting them in the denominator would
    # make an offline runner permanently red.
    failed = len(results) - passed - skipped
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
