---
name: mirror-run
description: >-
  Bridge virtual and physical experiments for scientific agents. Given a
  SwarmLabs scenario and observed data, build a GP surrogate in silico,
  optionally execute the same design on a real MHS-compatible instrument
  endpoint, and quantify the sim-to-real gap with uncertainty (UQ). Use when
  an agent needs to (a) decide the next physical experiment from virtual
  iteration, (b) validate a surrogate against real instrument data, or
  (c) emit an MHS-ready command manifest before touching hardware. Triggers:
  "mirror run", "virtual to real", "sim2real", "validate surrogate",
  "MHS bridge", "should I trust this virtual experiment", "bridge virtual
  and physical experiment".
license: MIT
compatibility: "Claude Code, Cursor, Codex, Gemini CLI, and any agentskills.io-compatible agent"
allowed-tools: "python bash curl"
metadata:
  version: "1.0"
  author: SwarmLabs
  category: scientific-computing
  requires: "Python 3.10+, network access to swarmlabs.tools/v3 (or a local v3 endpoint)"
  counterpart: "virtual-experiment-design"
---

# Mirror Run — virtual ↔ physical experiment bridge

You are helping a scientific agent move between **in-silico** and **physical**
experiments without losing track of uncertainty.

## When to Use

- An agent has iterated a design virtually (e.g. via `virtual-experiment-design`)
  and must now decide what to run on a **real instrument**.
- A surrogate model must be **validated against real data** before it is trusted.
- You must emit a **safe, reviewable command manifest** for a physical device
  (MHS-compatible) instead of letting an agent act blindly.

## Core principle

> A virtual experiment is cheap; a physical experiment is expensive and
> irreversible. The job of a mirror run is to quantify **how much you should
> trust the virtual answer before you spend the real reagent.**

Never present a virtual prediction as if it were a measurement. Always report
the GP **std** alongside the mean, and always gate physical action behind the
**VirtualLab Guard** verdict (`pass` / `controlled` / `reject`).

## How to run

The bundled script talks to the SwarmLabs v3 API (or any compatible endpoint):

```bash
python skills/agent-skills/mirror-run/scripts/mirror_run.py \
  --scenario micro_haldane_putida \
  --train-x-json '[[0.1],[0.5],[1.0],[2.0],[5.0]]' \
  --train-y-json '[0.11,0.26,0.28,0.20,0.11]' \
  --query-x-json '[[0.3],[0.8],[1.6]]' \
  --emit-mhs            # also print an MHS-ready command manifest
```

Optional: validate against a real instrument by passing `--real-endpoint`:

```bash
python skills/agent-skills/mirror-run/scripts/mirror_run.py \
  --scenario micro_haldane_putida \
  --train-x-json '[[0.1],[0.5],[1.0]]' --train-y-json '[0.11,0.26,0.28]' \
  --query-x-json '[[0.3],[0.8]]' \
  --real-endpoint https://lab.internal/mhs/tecan_fluent/sample
```

When `--real-endpoint` is omitted, the script runs a **virtual twin**
(perturbed by the scenario's published noise) to illustrate the sim2real gap
honestly — it does **not** claim a real measurement happened.

## Reading the output

- `virtual_mean` / `virtual_std` — GP surrogate prediction + uncertainty.
- `guard.status` — `pass` if the query lies inside the trusted region.
- `sim2real_delta` — mean absolute gap between virtual and (real or twin) values.
- `mhs_manifest` — a JSON command set an MHS driver could execute, tagged with
  the Guard verdict so a human/agent knows whether to proceed.

## Reference

- `references/mhs-bridge.md` — how SwarmLabs plugs into Anthropic's Model
  Hardware Standard as a *virtual instrument*.
- `references/microbiology-scenarios.md` — the 52 scenario catalog (shared with
  `virtual-experiment-design`).

## Guardrails

- If `guard.status` is `reject` for any query point, surface that explicitly
  and do **not** recommend physical action there.
- If `sim2real_delta` exceeds the scenario's published noise by a large margin,
  flag **model inadequacy**, not just noise.
- Never fabricate real-instrument results. A missing `--real-endpoint` means
  the "real" column is a virtual twin, labeled as such.
