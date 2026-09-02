# SwarmLabs Agent Skills

**Math-backed verification for scientific agents.**

Eight skills that let an agent design experiments, fit kinetics, and check
literature claims — while knowing *how confident it is and where the answer
breaks down*.

Most agent-skill collections for science stop at literature search, writing and
review. They produce text that reads well and carry no numeric account of their
own reliability. These skills produce **numbers with error bars and failure
boundaries**.

---

## Why this exists

Three questions an agent should be able to answer, and usually cannot:

1. **How confident is this prediction?** — not "fairly confident", but a
   posterior standard deviation.
2. **Is this condition inside the region we actually measured?** — a verdict,
   not a vague warning about extrapolation.
3. **Does this published number hold under *my* conditions?** — graded against
   a computed metric, not asserted.

Everything here is built to answer those three with arithmetic rather than
adjectives.

## The skills

| Skill | What it returns | Runs |
|---|---|---|
| [`virtual-experiment-design`](virtual-experiment-design/) | Ranked next-run conditions with 95% intervals | Online |
| [`uq-coverage-audit`](uq-coverage-audit/) | PASS / MARGINAL / OVERCONFIDENT with the coverage gap | Offline |
| [`ood-guard`](ood-guard/) | pass / controlled / reject per query point | Online or offline |
| [`kinetic-model-selection`](kinetic-model-selection/) | Best-supported kinetic form with fitted parameters | Online |
| [`fermentation-design`](fermentation-design/) | Full bioprocess design loop over 69 strains and 20 media | Online |
| [`strain-media-selection`](strain-media-selection/) | Ranked hosts with µ_max, Ks, and matched media | Online |
| [`paper-claim-validation`](paper-claim-validation/) | PASS / MARGINAL / UNVALIDATED with the supporting metric | Local reasoning |
| [`mirror-run`](mirror-run/) | Sim-to-real gap and an MHS-ready command manifest | Online |

## Install

```bash
# Agent Skills CLI
npx skills add lm203688/swarmlabs-skills

# GitHub CLI
gh skill install lm203688/swarmlabs-skills

# Or clone into your agent's skills directory
git clone https://github.com/lm203688/swarmlabs-skills.git ~/.agents/skills/swarmlabs
```

Each directory is self-contained and follows the
[Agent Skills specification](https://agentskills.io/specification), so it drops
into Claude Code, Cursor, Codex, Gemini CLI, or any compatible agent.

## Quick start

Check whether a model's uncertainty intervals are actually honest:

```bash
cd uq-coverage-audit
python scripts/uq_audit.py your_data.csv
```

```
Uncertainty Coverage Audit
----------------------------------------------
  observations          : 24
  nominal coverage      : 0.95
  empirical coverage    : 0.88
  gap                   : -0.07
  mean interval width   : 1.0740
----------------------------------------------
  VERDICT: MARGINAL
```

Check whether a proposed condition is inside the measured region:

```bash
cd ood-guard
python scripts/guard_local.py training.csv --query "37,7.0,6.0"
```

Both scripts need only `numpy`. No API key, no network, no account.

## What we deliberately do not claim

Honesty about scope is part of the product.

- **We are not the only ones doing "verification."** Other projects use the word
  for rubric scoring or audit trails. What is specific here is *quantified*
  verification: coverage, calibration, and out-of-distribution boundaries
  backed by computation.
- **The 3% noise floor is intentional.** Narrowing it produces prettier
  intervals and worse coverage. It is not a bug to be tuned away.
- **A `reject` verdict is a refusal, not a low-confidence answer.** Reporting a
  rejected prediction anyway is how bad numbers reach papers.
- **No grade without a number.** If a claim cannot be checked against computed
  evidence, the grade is `UNVALIDATED` — not `PASS` by analogy.

These skills publish their own weak results. Of the 18 benchmarks in the public
ledger, 10 are MARGINAL and **7 sit in the OOD red zone** with coverage below
0.5. That is reported at [swarmlabs.tools/verify](https://swarmlabs.tools/verify)
rather than filtered out, because a validation record with no failures in it
carries no information.

## Validation

Every skill is checked against the specification before release:

```bash
python tests/validate_skills.py
```

Checks the closed set of six top-level frontmatter keys, `name` matching the
directory, description length, `metadata` as a block mapping with a quoted
version, `allowed-tools` as a string, the 500-line limit, and permitted
directory entries. Exits non-zero on any violation, so it can gate CI.

## API

The online skills call the public SwarmLabs v3 API — read-only, no credentials:

```bash
curl -s https://swarmlabs.tools/api/v3/health
# {"ok":true,"api_version":"3.0.0","scenarios":63,"strains":69,"media":20,...}
```

Endpoints: `/scenarios`, `/strains`, `/strain/{id}`, `/media`, `/predict`,
`/fit`, `/guard`, `/submit-strain`.

> Note: the `/strains` and `/scenarios` list endpoints return `name` and
> `domain` fields in Chinese. Use `/strain/{id}` for full English taxonomy.

## License

MIT — see [LICENSE](LICENSE).
