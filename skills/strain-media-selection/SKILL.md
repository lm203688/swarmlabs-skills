---
name: strain-media-selection
description: Select a microbial host and culture medium from a curated database of 69 strains and 20 defined or complex media, using taxonomy, genome metadata, and growth kinetics rather than guesswork. Returns candidates with mu_max, Ks, yield, optimal temperature and pH, substrate profile and aerobic capability, matched to compatible media. Use when choosing an expression host, finding a strain with given kinetic parameters, or identifying a defined medium for a specific organism. Not for sourcing physical cultures or for pathogenicrisk classification.
license: MIT
compatibility: Requires network access to https://swarmlabs.tools/api/v3. Read-only, no credentials.
allowed-tools: Read Bash
metadata:
  version: "1.0"
  skill-author: SwarmLabs
  category: strain-selection
  assets: "69 strains (bacteria, yeast, fungi, archaea), 20 media"
  data-fields: "taxonomy, genome, growth_params, media_preference, conditions"
---

# Strain and Medium Selection

## When to Use

Choosing a host is the highest-leverage decision in a bioprocess, and it is
usually made from habit. The database makes it a query.

Triggers:

- "Which host should I use for X?"
- Finding a strain whose µ_max / Ks / temperature optimum fits a constraint
- Moving from complex to defined medium
- Checking whether a strain tolerates a given condition

Do **not** use this for ordering physical cultures, or as an authoritative
biosafety classification.

## Workflow

### 1. Survey the database

```bash
curl -s "https://swarmlabs.tools/api/v3/strains"
curl -s "https://swarmlabs.tools/api/v3/media"
```

Verified (2026-09-02): 69 strains, 20 media.

The list endpoint returns a compact projection — enough to shortlist:

```json
{"id": "ecoli_K12_MG1655", "name": "大肠杆菌", "genus": "Escherichia",
 "species": "coli", "mu_max": 0.81, "Ks": 0.004, "substrate": "glucose"}
```

> **Note:** the `name` and `domain` fields on the list endpoints are returned in
> Chinese. The per-strain detail endpoint carries full English
> `taxonomy.strain_name`, `genus`, and `species`, so prefer
> `/strain/{id}` for anything you will quote.

### 2. Get full detail on the shortlist

```bash
curl -s "https://swarmlabs.tools/api/v3/strain/ecoli_K12_MG1655"
```

Detail adds `genome` (accession, assembly level, size), `growth_params`
(`mu_max`, `Ks`, `Ki`, `Yxs`, `m`, `T_opt`, `p_opt`), `media_preference`,
`conditions` (aerobic / anaerobic), and `refs`.

### 3. Filter on the constraint that actually binds

| If the constraint is… | Filter on |
|---|---|
| Productivity | `mu_max` — spans ~0.1 to >1.0 h⁻¹ across the database |
| Dilution rate / washout | `Ks` — low Ks survives low substrate |
| Substrate availability | `substrate` field (glucose, glycerol, toluene, lactate…) |
| Temperature envelope | `T_opt` |
| Defined medium needed | `media_preference` containing a `minimal` type |
| Oxygen-limited process | `conditions.aerobic`, `anaerobic_flexible` |

### 4. Match medium to strain

```bash
curl -s "https://swarmlabs.tools/api/v3/media"
```

Each medium carries `type` (`complex` or `minimal`) and `ph`. Defined media
matter when reproducibility or downstream purification is the constraint;
complex media usually win on growth rate. That trade-off is the actual decision
— state it rather than defaulting to LB.

## Reporting Rules

1. **Report µ_max with its substrate.** µ_max is meaningless without saying what
   the strain is growing on.
2. **Distinguish `complex` from `minimal`** whenever recommending a medium. It
   changes experimental reproducibility.
3. **Cite the genome accession** when identity matters. Strain names are
   ambiguous; accessions are not.
4. **Say when the database has no good match.** Proposing the closest strain
   while implying it is suitable is worse than reporting the gap.

## Caveats

- Kinetic parameters are **literature values for the type strain under stated
  conditions**. Your isolate will differ. Use them for shortlisting, not for
  design calculations without fitting your own data.
- `Ki` is `null` for many strains — that is missing data, not a claim that
  inhibition is absent.
- The database is a snapshot. It does not track taxonomic reclassification after
  its build date.
- Growth kinetics and product formation are different optima. A fast grower is
  not automatically the best producer.

## References

- Strain database: `https://swarmlabs.tools/api/v3/strains`
- Medium database: `https://swarmlabs.tools/api/v3/media`
- Per-strain detail: `https://swarmlabs.tools/api/v3/strain/{id}`
