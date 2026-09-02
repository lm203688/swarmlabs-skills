---
name: fermentation-design
description: Design and iterate bioprocess experiments in silico by combining a 69-strain database, 20 defined media, and 52 literature-referenced fermentation scenarios with Gaussian-process active learning. Returns ranked next-run conditions with uncertainty intervals and out-of-distribution verdicts instead of generic DOE guidance. Use for fed-batch and chemostat design, induction and feeding strategy, scale-up parameter screening, or any limited-budget bioprocess optimization. Not for equipment control or GMP batch release.
license: MIT
compatibility: Requires network access to https://swarmlabs.tools/api/v3. Read-only, no credentials for scenarios, strains, media, predict, fit, guard.
allowed-tools: Read Write Bash
metadata:
  version: "1.0"
  skill-author: SwarmLabs
  category: bioprocess-optimization
  assets: "69 strains, 20 media, 63 scenarios (52 microbiology)"
  engine: "GP surrogate + active learning + OOD guard"
---

# Fermentation Design

## When to Use

Fermentation optimization is expensive in calendar time. The point of this skill
is to spend **hours of compute instead of weeks of bench time** deciding which
few runs are worth doing.

Triggers:

- Designing a fed-batch or chemostat run
- Choosing induction point, inducer level, feeding rate, temperature, pH, DO setpoint
- Screening a new strain/medium combination before committing a campaign
- Deciding the next 3 runs when only 5–10 data points exist

Do **not** use for GMP batch release, equipment control, or regulatory
submission. This is a design tool.

## Workflow

### 0. Start from the strain, not the parameter grid

```bash
curl -s "https://swarmlabs.tools/api/v3/strain/ecoli_K12_MG1655"
```

Verified response fields (2026-09-02):

```json
{
  "strain_id": "ecoli_K12_MG1655",
  "taxonomy": {"domain": "Bacteria", "genus": "Escherichia", "species": "coli",
               "strain_name": "K-12 MG1655"},
  "genome": {"accession": "U00096.3", "genome_size_Mbp": 4.64},
  "growth_params": {"substrate": "glucose", "mu_max": 0.81, "Ks": 0.004,
                    "Yxs": 0.5, "m": 0.02, "T_opt": 37, "p_opt": 7},
  "media_preference": ["LB", "M9_minimal", "TB_terrific"],
  "conditions": {"aerobic": true, "anaerobic_flexible": true}
}
```

µ_max, Ks, T_opt and p_opt give you a **physiological centre of gravity**. Design
around it, don't grid blindly.

```bash
curl -s "https://swarmlabs.tools/api/v3/strains"     # 69 strains
curl -s "https://swarmlabs.tools/api/v3/media"       # 20 media
curl -s "https://swarmlabs.tools/api/v3/scenarios"   # 63 scenarios
```

### 1. Pick a scenario covering the regime you care about

52 microbiology scenarios span Monod, Andrews, Contois, Haldane, Pirt,
Luedeking-Piret, Tessier, Baranyi, fed-batch, chemostat, Thiele modulus,
antibiotic kill, and thermal death kinetics.

```bash
curl -s "https://swarmlabs.tools/api/v3/scenarios" \
  | jq '.scenarios[] | select(.key | startswith("micro_")) | .key, .domain, .bounds'
```

Each entry carries the published formula, parameter bounds, and literature
references — so you can cite what you're simulating.

### 2. Establish a prior from literature parameters

Scenario metadata gives published parameter sets. Use them as the starting
surrogate **before** spending any bench time, then update with your own data.
Published values are priors for the literature strain — not for your production
strain. Say so when reporting.

### 3. Rank candidate conditions

```bash
curl -s -X POST "https://swarmlabs.tools/api/v3/predict" \
  -H "Content-Type: application/json" \
  -d '{
    "scenario": "microbio_monod",
    "train_x": [[0.1],[0.5],[1.0],[2.0],[5.0]],
    "train_y": [0.15, 0.42, 0.58, 0.71, 0.79],
    "query_x": [[0.3],[1.5],[3.0]]
  }'
```

Returns `mean`, `std`, and `dist` per candidate.

### 4. Guard the candidates — always

```bash
curl -s -X POST "https://swarmlabs.tools/api/v3/guard" -H "Content-Type: application/json" -d @payload.json
```

Discard `reject` candidates. Report `controlled` ones with their wider interval.
See the `ood-guard` skill for the reporting obligations.

### 5. Choose explore vs exploit, and say which

- **Exploit** (highest predicted mean) — the goal is hitting a titer target
- **Explore** (highest posterior `std`) — the goal is improving the model

A sensible default for a 3-run batch: top 2 by mean, top 1 by std. Stating the
strategy matters because the two produce different-looking recommendations and
someone will ask why.

### 6. Close the loop

Feed the measured results back as `train_x` / `train_y` and repeat. The
surrogate is not a report — it is a running model that gets sharper each cycle.

## Reporting Rules

1. **Never report a mean without its interval.** "Titer 4.2 g/L" is half an
   answer. "4.2 ± 0.3 g/L (95%)" is an answer.
2. **Name the regime boundary.** Where the model stops being valid is often more
   valuable than where the optimum is.
3. **State the strain and medium** the design assumes. µ_max differs by an
   order of magnitude across the 69 strains.
4. **Recommend the fewest runs that answer the question.** A design that needs
   30 runs is not a design.

## Caveats

- Scenario parameters are **literature values**, not fits to your strain. Treat
  as priors.
- Below ~5 points per input dimension, intervals are wide. Say so rather than
  reporting a confident-looking optimum.
- Scale-up introduces gradients (mixing, oxygen transfer) that a
  well-mixed surrogate cannot see. A lab-scale optimum is a starting point for
  scale-down studies, not a scale-up recipe.
- This is in-silico. A virtual result tells you where to look, not what is true.

## References

- Strain and medium databases: `https://swarmlabs.tools/api/v3/strains`, `/media`
- Published coverage and OOD figures: <https://swarmlabs.tools/verify>
