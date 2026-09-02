#!/usr/bin/env python3
"""Validate SwarmLabs agent skills against the Agent Skills specification.

Rules enforced (from the agentskills.io specification, cross-checked against
K-Dense-AI/scientific-agent-skills — the largest skill repo in the ecosystem):

  1. SKILL.md exists and frontmatter is delimited by --- fences.
  2. Top-level frontmatter keys are within the closed set of six:
     name, description, license, compatibility, allowed-tools, metadata.
     Anything else must live under `metadata`.
  3. `name` is 1-64 chars, lowercase letters / digits / hyphens only, and
     matches the containing directory name.
  4. `description` is non-empty and <= 1024 chars.
  5. `metadata` is written as a YAML block mapping (not a JSON flow mapping).
     A flow mapping makes strictyaml fail the whole document, so the skill
     never registers.
  6. `metadata.version` exists and is quoted (an unquoted 1.0 parses as a
     float and some loaders reject it).
  7. `allowed-tools` is a space-separated string, not a YAML list.
  8. SKILL.md is under 500 lines.
  9. Required documentation sections are present.
 10. Directory contains only the four permitted entry types:
     SKILL.md, references/, scripts/, assets/.

Exit code 0 when every skill passes, 1 otherwise.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CLOSED_TOP_LEVEL = {
    "name",
    "description",
    "license",
    "compatibility",
    "allowed-tools",
    "metadata",
}

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_DESCRIPTION = 1024
MAX_LINES = 500
REQUIRED_SECTIONS = ["when to use"]
PERMITTED_ENTRIES = {"SKILL.md", "references", "scripts", "assets"}


def split_frontmatter(text: str):
    """Return (frontmatter_lines, body) or (None, text) when absent/invalid."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], "\n".join(lines[i + 1 :])
    return None, text


def parse_top_level(fm_lines: list[str]) -> dict:
    """Minimal top-level parser: key -> raw remainder, plus child block lines."""
    out: dict = {}
    current = None
    for raw in fm_lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith((" ", "\t")):
            if current is not None:
                out[current]["children"].append(raw)
            continue
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", raw)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        current = key
        out[key] = {"value": val, "children": []}
    return out


def validate_skill(skill_dir: Path) -> list[str]:
    errors: list[str] = []
    rel = skill_dir.name
    skill_md = skill_dir / "SKILL.md"

    if not skill_md.exists():
        return [f"{rel}: SKILL.md is missing"]

    text = skill_md.read_text(encoding="utf-8")
    fm, body = split_frontmatter(text)
    if fm is None:
        return [f"{rel}: no valid --- frontmatter block"]

    fields = parse_top_level(fm)

    # 2. closed top-level key set
    extra = set(fields) - CLOSED_TOP_LEVEL
    if extra:
        errors.append(
            f"{rel}: top-level keys outside the closed set: {sorted(extra)} "
            f"(move them under metadata)"
        )

    # 3. name
    if "name" not in fields:
        errors.append(f"{rel}: missing required field 'name'")
    else:
        name = fields["name"]["value"].strip()
        if not NAME_RE.match(name):
            errors.append(
                f"{rel}: name '{name}' must be lowercase letters, digits and hyphens only"
            )
        if not (1 <= len(name) <= 64):
            errors.append(f"{rel}: name length {len(name)} outside 1-64")
        if name != rel:
            errors.append(f"{rel}: name '{name}' does not match directory name")

    # 4. description
    if "description" not in fields:
        errors.append(f"{rel}: missing required field 'description'")
    else:
        desc = fields["description"]["value"].strip()
        # Fold continuation lines for block scalars (>- / |).
        cont = [c.strip() for c in fields["description"]["children"]]
        full = (desc + " " + " ".join(cont)).strip()
        if not full or full in (">-", "|", ">"):
            errors.append(f"{rel}: description is empty")
        elif len(full) > MAX_DESCRIPTION:
            errors.append(
                f"{rel}: description is {len(full)} chars (limit {MAX_DESCRIPTION})"
            )

    # 5 + 6. metadata must be a block mapping with a quoted version
    if "metadata" not in fields:
        errors.append(f"{rel}: missing required field 'metadata'")
    else:
        if fields["metadata"]["value"].strip():
            errors.append(
                f"{rel}: metadata must be a block mapping, not a flow mapping "
                f"(found inline value '{fields['metadata']['value'][:40]}')"
            )
        children = fields["metadata"]["children"]
        if not children:
            errors.append(f"{rel}: metadata block is empty")
        else:
            has_version = False
            for c in children:
                m = re.match(r"^\s+([A-Za-z0-9_-]+):\s*(.*)$", c)
                if not m:
                    continue
                k, v = m.group(1), m.group(2).strip()
                if k == "version":
                    has_version = True
                    if not (v.startswith('"') and v.endswith('"')):
                        errors.append(
                            f"{rel}: metadata.version must be quoted, got '{v}'"
                        )
            if not has_version:
                errors.append(f"{rel}: metadata.version is required and must be quoted")

    # 7. allowed-tools must be a string
    if "allowed-tools" in fields:
        at = fields["allowed-tools"]
        if at["children"]:
            errors.append(
                f"{rel}: allowed-tools must be a space-separated string, not a YAML list"
            )

    # 8. length
    n_lines = len(text.split("\n"))
    if n_lines >= MAX_LINES:
        errors.append(
            f"{rel}: SKILL.md has {n_lines} lines (limit {MAX_LINES}); "
            f"move long content to references/"
        )

    # 9. required sections (heading level and case are not mandated by the spec)
    headings = [
        re.sub(r"^#+\s*", "", ln).strip().lower()
        for ln in body.split("\n")
        if ln.lstrip().startswith("#")
    ]
    for sec in REQUIRED_SECTIONS:
        if sec not in headings:
            errors.append(f"{rel}: missing required section '## When to Use'")

    # 10. permitted entries only
    for entry in skill_dir.iterdir():
        if entry.name.startswith("."):
            continue
        if entry.name not in PERMITTED_ENTRIES:
            errors.append(
                f"{rel}: '{entry.name}' is not permitted in a skill directory "
                f"(allowed: {sorted(PERMITTED_ENTRIES)}; tests belong in tests/)"
            )

    return errors


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    skill_dirs = sorted(
        d for d in root.iterdir() if d.is_dir() and (d / "SKILL.md").exists()
    )

    if not skill_dirs:
        print("error: no skill directories found", file=sys.stderr)
        return 1

    all_errors: dict[str, list[str]] = {}
    for d in skill_dirs:
        errs = validate_skill(d)
        if errs:
            all_errors[d.name] = errs

    print(f"Validating {len(skill_dirs)} skills\n" + "-" * 52)
    for d in skill_dirs:
        n_lines = len((d / "SKILL.md").read_text(encoding="utf-8").split("\n"))
        status = "FAIL" if d.name in all_errors else "PASS"
        print(f"  [{status}] {d.name:<28} {n_lines:>4} lines")

    if all_errors:
        print("\nProblems:")
        for name, errs in all_errors.items():
            for e in errs:
                print(f"  - {e}")
        print(f"\n{len(all_errors)} of {len(skill_dirs)} skills failed validation")
        return 1

    print(f"\nAll {len(skill_dirs)} skills pass specification validation")
    return 0


if __name__ == "__main__":
    sys.exit(main())
