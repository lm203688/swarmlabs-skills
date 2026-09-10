#!/usr/bin/env python3
"""Shortlist production hosts and media offline, from a 69-strain table.

Filter on the constraint that actually binds — temperature, pH, oxygen, or
carbon source — and rank survivors by the growth rate they can actually sustain
at *your* operating point, using Monod kinetics rather than the catalogue
maximum.

Everything is local. No API key, no network, no account. numpy optional; the
script runs on the standard library alone.

Usage:
    python select_strain.py --temp 37 --ph 7.0
    python select_strain.py --temp 50 --ph 6.5 --substrate glucose --aerobic
    python select_strain.py --substrate glycerol --top 10 --json
    python select_strain.py --list-substrates

Exit codes:
    0  shortlist produced
    1  input error
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "..", "assets", "strains_min.json")

# Growth falls off away from T_opt / pH_opt. These widths are the usual first
# approximation for mesophiles; they are screening constants, not fitted values.
T_TOLERANCE = 8.0     # deg C, Gaussian sigma
PH_TOLERANCE = 1.2    # pH units, Gaussian sigma


def load_db() -> list[dict]:
    path = os.path.normpath(DB)
    if not os.path.exists(path):
        raise FileNotFoundError(f"strain table missing: {path}")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("strains", [])


def temp_factor(T_opt: float, T: float) -> float:
    if T_opt is None:
        return 1.0
    return float(math.exp(-((T - T_opt) ** 2) / (2 * T_TOLERANCE ** 2)))


def ph_factor(p_opt: float, pH: float) -> float:
    if p_opt is None:
        return 1.0
    return float(math.exp(-((pH - p_opt) ** 2) / (2 * PH_TOLERANCE ** 2)))


def monod(mu_max: float, Ks: float, S: float) -> float:
    """Specific growth rate at substrate concentration S (g/L)."""
    if mu_max is None:
        return 0.0
    if Ks is None:
        return float(mu_max)
    if S is None:
        return float(mu_max)
    return float(mu_max * S / (Ks + S))


def score(strain: dict, T: float | None, pH: float | None,
          substrate: float | None) -> tuple[float, dict]:
    gp_mu = strain.get("mu_max")
    if gp_mu is None:
        return -1.0, {}

    tf = temp_factor(strain.get("T_opt"), T) if T is not None else 1.0
    pf = ph_factor(strain.get("p_opt"), pH) if pH is not None else 1.0
    # Monod derate: at a realistic residual substrate the rate is below mu_max.
    sf = monod(1.0, strain.get("Ks"), substrate) if substrate is not None else 1.0
    if substrate is not None and strain.get("Ks") is None:
        sf = 0.7  # unknown affinity: assume a modest penalty, not full rate

    mu_eff = float(gp_mu) * tf * pf * sf
    detail = {
        "mu_catalog": round(float(gp_mu), 4),
        "mu_effective": round(mu_eff, 4),
        "temp_factor": round(tf, 4),
        "ph_factor": round(pf, 4),
        "substrate_factor": round(sf, 4),
        "T_opt": strain.get("T_opt"),
        "pH_opt": strain.get("p_opt"),
        "Ks": strain.get("Ks"),
        "Yxs": strain.get("Yxs"),
        "substrate": strain.get("substrate"),
        "media": strain.get("media") or [],
    }
    return mu_eff, detail


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Shortlist hosts and media for an operating window."
    )
    ap.add_argument("--temp", type=float, help="Operating temperature, deg C")
    ap.add_argument("--ph", type=float, help="Operating pH")
    ap.add_argument(
        "--substrate", type=float, default=None,
        help="Residual substrate concentration g/L for the Monod derate",
    )
    ap.add_argument("--carbon-source", help="Filter by carbon source name")
    ap.add_argument("--aerobic", action="store_true", help="Require aerobic growth")
    ap.add_argument(
        "--anaerobic", action="store_true", help="Require anaerobic-capable strains"
    )
    ap.add_argument("--top", type=int, default=8, help="Shortlist size (default 8)")
    ap.add_argument("--json", action="store_true", help="Emit JSON")
    ap.add_argument("--list-substrates", action="store_true",
                    help="Print available carbon sources and exit")
    args = ap.parse_args()

    try:
        strains = load_db()
    except (OSError, ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.list_substrates:
        subs = sorted({s.get("substrate") for s in strains if s.get("substrate")})
        print(f"{len(subs)} carbon sources across {len(strains)} strains:")
        for s in subs:
            n = sum(1 for x in strains if x.get("substrate") == s)
            print(f"  {s:<22} {n}")
        return 0

    if all(v is None for v in (args.temp, args.ph, args.substrate)) and not (
        args.carbon_source or args.aerobic or args.anaerobic
    ):
        print(
            "error: give at least one constraint "
            "(--temp / --ph / --substrate / --carbon-source / --aerobic)",
            file=sys.stderr,
        )
        return 1

    pool = strains
    if args.carbon_source:
        want = args.carbon_source.lower()
        pool = [s for s in pool if (s.get("substrate") or "").lower() == want]
    if args.aerobic:
        pool = [s for s in pool if s.get("aerobic")]
    if args.anaerobic:
        pool = [s for s in pool if s.get("anaerobic_flexible")]

    if not pool:
        print("error: no strain matches those hard filters", file=sys.stderr)
        print("       try --list-substrates, or relax --aerobic/--anaerobic",
              file=sys.stderr)
        return 1

    scored = []
    for s in pool:
        mu_eff, detail = score(s, args.temp, args.ph, args.substrate)
        if mu_eff < 0:
            continue
        scored.append((mu_eff, s, detail))
    scored.sort(key=lambda t: -t[0])

    rows = []
    for mu_eff, s, detail in scored[: args.top]:
        rows.append(
            {
                "strain_id": s.get("strain_id"),
                "organism": " ".join(
                    p for p in (s.get("genus"), s.get("species")) if p
                ),
                "common_name": s.get("common_name"),
                **detail,
            }
        )

    payload = {
        "constraints": {
            "temp_C": args.temp,
            "pH": args.ph,
            "substrate_g_L": args.substrate,
            "carbon_source": args.carbon_source,
            "aerobic": args.aerobic or None,
            "anaerobic": args.anaerobic or None,
        },
        "pool_size": len(pool),
        "total_strains": len(strains),
        "shortlist": rows,
    }

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print("Strain and Medium Selection")
    print("-" * 74)
    c = payload["constraints"]
    bits = []
    if c["temp_C"] is not None:
        bits.append(f"T={c['temp_C']}C")
    if c["pH"] is not None:
        bits.append(f"pH={c['pH']}")
    if c["substrate_g_L"] is not None:
        bits.append(f"S={c['substrate_g_L']} g/L")
    if c["carbon_source"]:
        bits.append(f"carbon={c['carbon_source']}")
    if c["aerobic"]:
        bits.append("aerobic")
    if c["anaerobic"]:
        bits.append("anaerobic-capable")
    print(f"  constraints : {', '.join(bits) if bits else '(none)'}")
    print(f"  pool        : {len(pool)} / {len(strains)} strains")
    print("-" * 74)
    print(f"  {'mu_eff':>7} {'mu_cat':>7}  {'T_opt':>5} {'pH':>4}  organism")
    for r in rows:
        org = r["organism"] or r["strain_id"]
        to = f"{r['T_opt']:.4g}" if r["T_opt"] is not None else "-"
        po = f"{r['pH_opt']:.4g}" if r["pH_opt"] is not None else "-"
        print(f"  {r['mu_effective']:>7.4f} {r['mu_catalog']:>7.4f}  {to:>5} {po:>4}  {org}")
    print("-" * 74)
    if rows:
        r = rows[0]
        print(f"  Best: {r['organism'] or r['strain_id']} "
              f"(mu_eff {r['mu_effective']:.4f} /h)")
        if r["media"]:
            print(f"  Media: {', '.join(r['media'][:4])}")
        if args.temp is not None or args.ph is not None:
            print(f"  Derated from catalogue mu_max {r['mu_catalog']:.4f} by "
                  f"T x{r['temp_factor']:.3f}, pH x{r['ph_factor']:.3f}")
    print()
    print("  mu_eff is a screening estimate at your operating point, not a")
    print("  measured rate. Shortlist with it, then confirm on the bench.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
