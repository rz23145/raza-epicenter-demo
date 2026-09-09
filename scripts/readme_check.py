#!/usr/bin/env python3
"""Verify every number in the README's Results section against exports.

The section starts at the "## Results" heading and ends at the next "## "
heading. Every number in it must appear on some line of a file under
data/exports/ (csv, md, txt, json). The trace (number, file, line) is
printed so a reviewer can follow each claim to its source.

Failure modes, all exit nonzero:
- README has no "## Results" heading.
- The Results section contains zero numbers: a results section that claims
  nothing is vacuous, not safe.
- Any number cannot be traced to an export file.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
EXPORTS = REPO_ROOT / "data" / "exports"

HEADING = "## Results"
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
EXPORT_SUFFIXES = {".csv", ".md", ".txt", ".json"}


def results_section(text: str) -> str | None:
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == HEADING:
            start = i + 1
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    return "\n".join(lines[start:end])


def trace_number(number: str, files: list[Path]) -> tuple[Path, int] | None:
    for path in files:
        for line_no, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
        ):
            if number in line:
                return path, line_no
    return None


def main() -> int:
    text = README.read_text(encoding="utf-8")
    section = results_section(text)
    if section is None:
        print("readme-check: FAIL. README has no '## Results' heading.")
        return 1
    numbers = sorted(set(NUMBER_RE.findall(section)))
    if not numbers:
        print(
            "readme-check: FAIL. The Results section contains no numbers."
            " An empty results section is vacuous; either report traced"
            " numbers or state explicitly why there are none, with the"
            " numbers that support that statement."
        )
        return 1
    export_files = (
        sorted(
            p
            for p in EXPORTS.rglob("*")
            if p.is_file() and p.suffix in EXPORT_SUFFIXES
        )
        if EXPORTS.exists()
        else []
    )
    missing: list[str] = []
    for number in numbers:
        found = trace_number(number, export_files)
        if found is None:
            missing.append(number)
            print(f"  {number:>12}  -> NOT FOUND in any export file")
        else:
            path, line_no = found
            rel = path.relative_to(REPO_ROOT)
            print(f"  {number:>12}  -> {rel}:{line_no}")
    if missing:
        print(
            f"readme-check: FAIL. {len(missing)} number(s) in the Results"
            f" section trace to no export file: {', '.join(missing)}"
        )
        return 1
    print(
        f"readme-check: all {len(numbers)} result numbers traced to exports. OK."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
