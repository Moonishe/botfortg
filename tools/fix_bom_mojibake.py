#!/usr/bin/env python3
"""Fix BOM + double-encoded UTF-8 mojibake in Python source files.

Encoding corruption: original UTF-8 Russian text was read as CP1251,
then re-encoded as UTF-8 with BOM added. This undoes that corruption.

Algorithm (per file):
  1. Strip BOM if present
  2. Decode as UTF-8
  3. For each line, try reverse-encoding (encode cp1251 → decode utf-8)
     - Skip lines that fail (non-CP1251 chars like em dash, emoji)
     - Skip lines that don't produce more Cyrillic after fix
  4. Write back without BOM
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# Mojibake indicator: 'Р' or 'С' (U+0420/U+0421 — first bytes of double-encoded D0/D1)
# followed by a non-ASCII, non-whitespace character
MOJIBAKE_RE = re.compile(r"[РС][^\x00-\x7F\s]")

# Real Cyrillic after fix
CYRILLIC_RE = re.compile(r"[А-ЯЁа-яё]")


def has_mojibake(text: str) -> bool:
    """Check if text contains double-encoding mojibake patterns."""
    return bool(MOJIBAKE_RE.search(text))


def count_cyrillic(text: str) -> int:
    """Count real Cyrillic characters."""
    return len(CYRILLIC_RE.findall(text))


def fix_line(line: str) -> str:
    """Fix a single line if it contains mojibake. Returns original if unfixable."""
    if not has_mojibake(line):
        return line

    try:
        fixed = line.encode("cp1251").decode("utf-8")
        # Verify: after fix should have more Cyrillic AND no mojibake
        if count_cyrillic(fixed) > count_cyrillic(line) and not has_mojibake(fixed):
            return fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass

    # Line-level fix failed — try word-level
    # Split on non-Cyrillic/Latin boundaries, fix each segment
    words = re.split(r"(\s+)", line)
    for i, word in enumerate(words):
        if has_mojibake(word):
            try:
                fixed_word = word.encode("cp1251").decode("utf-8")
                if count_cyrillic(fixed_word) > 0:
                    words[i] = fixed_word
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
    return "".join(words)


def fix_file(filepath: Path, dry_run: bool = False) -> tuple[bool, str]:
    """Fix one file. Returns (changed, reason)."""
    try:
        raw = filepath.read_bytes()
    except Exception as e:
        return False, f"read error: {e}"

    # Strip BOM
    has_bom = raw[:3] == b"\xef\xbb\xbf"
    content = raw[3:] if has_bom else raw

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as e:
        return False, f"UTF-8 decode error: {e}"

    if not has_bom and not has_mojibake(text):
        return False, "no BOM, no mojibake"

    # Fix line by line
    lines = text.split("\n")
    fixed_lines = [fix_line(line) for line in lines]
    fixed = "\n".join(fixed_lines)

    # Check if anything changed
    if fixed == text and not has_bom:
        return False, "no changes needed"

    # Write back WITHOUT BOM
    if not dry_run:
        filepath.write_text(fixed, encoding="utf-8")

    cyr_before = count_cyrillic(text)
    cyr_after = count_cyrillic(fixed)
    bom_status = "BOM removed" if has_bom else "no BOM"
    return True, f"{bom_status}, cyrillic: {cyr_before}→{cyr_after}"


def main():
    parser = argparse.ArgumentParser(description="Fix BOM + mojibake in Python files")
    parser.add_argument("paths", nargs="*", default=["src"], help="Files/dirs to fix")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show per-file details"
    )
    args = parser.parse_args()

    files: list[Path] = []
    for path_str in args.paths:
        p = Path(path_str)
        if p.is_file() and p.suffix == ".py":
            files.append(p)
        elif p.is_dir():
            files.extend(sorted(p.rglob("*.py")))

    total = len(files)
    fixed = 0
    skipped = 0
    errors = 0

    for fp in files:
        try:
            changed, reason = fix_file(fp, dry_run=args.dry_run)
            if changed:
                fixed += 1
                if args.verbose:
                    print(f"  FIXED: {fp} — {reason}")
                else:
                    print(f"  FIXED: {fp}")
            else:
                skipped += 1
                if args.verbose:
                    print(f"  SKIP:  {fp} — {reason}")
        except Exception as e:
            errors += 1
            print(f"  ERROR: {fp} — {e}", file=sys.stderr)

    print(
        f"\n{'[DRY RUN] ' if args.dry_run else ''}Total: {total}, Fixed: {fixed}, Skipped: {skipped}, Errors: {errors}"
    )


if __name__ == "__main__":
    main()
