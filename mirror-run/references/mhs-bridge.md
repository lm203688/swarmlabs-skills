# SwarmLabs as a virtual instrument under MHS

Anthropic's **Model Hardware Standard (MHS)** (research preview 2026-08-27)
lets AI agents discover and control any programmable physical device through a
uniform driver: `read` / `write` primitives, auto-discovery, natural-language
tags, and an auto-generated reference file with safety limits. Early results:
QuEra laser-lock recovery 99.3%; CMU 3× speedup across incompatible devices.

## Why this is good for SwarmLabs

MHS removes the *execution* bottleneck (agents can now run real experiments).
That makes the *decision* bottleneck — what to run next, and how much to trust
the result — the scarce resource. SwarmLabs owns exactly that layer:

- **GP surrogate + active learning** → choose the next experiment point.
- **UQ (uncertainty quantification)** → state *how sure* the model is.
- **VirtualLab Guard** → `pass` / `controlled` / `reject` gate before any
  physical action.

The 52 microbiology scenarios + `/v3/sample` + `/v3/predict` + `/v3/guard`
map cleanly onto MHS's driver contract (see `docs/mhs-virtual-instrument.md`).

## What mirror-run does

`mirror_run.py` is the bridge:

1. Build a GP surrogate from observed data (`/v3/predict`).
2. Gate each query point behind VirtualLab Guard (`/v3/guard`).
3. Optionally execute the same design on a real `--real-endpoint` (MHS-compatible).
4. Quantify `sim2real_delta` and emit an **MHS-ready command manifest**.

Without `--real-endpoint`, it runs a labeled *virtual twin* — it never
fabricates a real measurement.

## See also

- `docs/mhs-virtual-instrument.md` — full adapter design.
- `references/microbiology-scenarios.md` (shared with `virtual-experiment-design`)
  — the 52 scenario catalog.
