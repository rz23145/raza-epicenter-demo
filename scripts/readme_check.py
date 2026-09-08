#!/usr/bin/env python3
"""Fail if the README claims result numbers that no export file contains.

Numbers inside the section delimited by <!-- results:begin --> and
<!-- results:end --> must each appear in at least one file under
data/exports/. If the README has no results section, or the section contains
no numbers, the check passes: a README that claims nothing cannot overclaim.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
EXPORTS = REPO_ROOT / "data" / "exports"

BEGIN = "<!-- results:begin -->"
END = "<!-- results:end -->"

NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def main() -> int:
    text = README.read_text(encoding="utf-8")
    if BEGIN not in text or END not in text:
        print("readme-check: no results section in README; nothing to verify. OK.")
        return 0
    section = text.split(BEGIN, 1)[1].split(END, 1)[0]
    numbers = set(NUMBER_RE.findall(section))
    if not numbers:
        print("readme-check: results section contains no numbers. OK.")
        return 0
    haystack = ""
    if EXPORTS.exists():
        for path in EXPORTS.rglob("*"):
            if path.is_file() and path.suffix in {".csv", ".md", ".txt", ".json"}:
                haystack += path.read_text(encoding="utf-8", errors="replace")
    missing = sorted(n for n in numbers if n not in haystack)
    if missing:
        print(
            "readme-check: FAIL. These numbers appear in the README results"
            f" section but in no export file: {', '.join(missing)}"
        )
        return 1
    print(f"readme-check: all {len(numbers)} result numbers traced to exports. OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
