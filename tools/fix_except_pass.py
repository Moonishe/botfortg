#!/usr/bin/env python3
"""SAFE replacement of `except Exception: pass` with logger.debug.

Scans all .py files under src/, finds lines where an `except Exception: pass`
pattern silently swallows errors, and replaces `pass` with a debug log call.

Protected files and intentional-silence comments are preserved.
"""

from __future__ import annotations

import argparse
import pathlib
import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"

# Files to NEVER modify
PROTECTED_FILES: set[str] = {
    "core/actions/mcp_env.py",
    "core/infra/telemetry.py",
}

# Comments on the PASS line that indicate INTENTIONAL silence
INTENTIONAL_COMMENTS: list[str] = [
    "intentionally",
    "expected",
    "best-effort",
    "best effort",
    "cleanup",
    "hooks are optional",
    "hooks optional",
    "never fail",
    "never-fail",
    "deliberately",
    "by design",
    "intended",
]

# Regex for an `except Exception...` line (handles `except Exception as e:` etc.)
EXCEPT_EX = re.compile(r"^(\s*)except\s+Exception(\s+as\s+\w+)?\s*:\s*(#.*)?$")

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class FixCandidate:
    file_path: pathlib.Path
    rel_path: str
    line_no: int  # 1-based line number of the except line
    indent: str
    pass_line: str  # full pass line
    pass_line_no: int


@dataclass
class FileFix:
    candidates: list[FixCandidate] = field(default_factory=list)
    needs_logging_import: bool = False
    needs_logger_init: bool = False


@dataclass
class Stats:
    scanned: int = 0
    skipped_protected: int = 0
    skipped_comment: int = 0
    skipped_noqa: int = 0
    skipped_finally: int = 0
    skipped_del_atexit: int = 0
    fixed: int = 0
    files_changed: int = 0
    edge_cases: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _is_in_finally(lines: list[str], lineno: int) -> bool:
    """Check if line is inside a `finally:` block."""
    for i in range(lineno - 1, -1, -1):
        stripped = lines[i].strip()
        if stripped.startswith("finally") and stripped.rstrip(":").strip() == "finally":
            # Check if this finally is at the same or outer indentation level
            curr_indent = len(lines[lineno]) - len(lines[lineno].lstrip())
            fin_indent = len(lines[i]) - len(lines[i].lstrip())
            if fin_indent <= curr_indent:
                return True
        # If we encounter a try/except at same level, stop looking
        if stripped.startswith("try") and stripped.rstrip(":").strip() == "try":
            curr_indent = len(lines[lineno]) - len(lines[lineno].lstrip())
            try_indent = len(lines[i]) - len(lines[i].lstrip())
            if try_indent <= curr_indent:
                return False
    return False


def _is_in_del_or_atexit(lines: list[str], lineno: int) -> bool:
    """Check if the except is inside __del__ or atexit handlers."""
    for i in range(lineno - 1, max(lineno - 50, -1), -1):
        stripped = lines[i].strip()
        if "def __del__" in stripped:
            return True
        if "atexit.register" in stripped:
            return True
    return False


def find_candidates(
    file_path: pathlib.Path,
) -> tuple[list[FixCandidate], dict[str, int]]:
    """Find all `except Exception: pass` patterns that should be fixed.
    Returns (candidates, skip_counts) where skip_counts tracks why items were skipped.
    """
    text = file_path.read_text(encoding="utf-8")
    lines = text.split("\n")
    candidates: list[FixCandidate] = []
    skip_counts: dict[str, int] = {
        "comment": 0,
        "noqa": 0,
        "finally": 0,
        "del_atexit": 0,
    }

    for i, line in enumerate(lines):
        m = EXCEPT_EX.match(line)
        if not m:
            continue

        if i + 1 >= len(lines):
            continue

        next_line = lines[i + 1]
        next_stripped = next_line.strip()

        # Must be `pass` (maybe with inline comment)
        if not (
            next_stripped == "pass"
            or next_stripped.startswith("pass  #")
            or next_stripped.startswith("pass #")
            or next_stripped.startswith("pass\t#")
        ):
            continue

        # Skip noqa / pylint
        if "noqa" in line.lower() or "pylint" in line.lower():
            skip_counts["noqa"] += 1
            continue

        # Skip `finally` blocks
        if _is_in_finally(lines, i):
            skip_counts["finally"] += 1
            continue

        # Skip __del__ / atexit
        if _is_in_del_or_atexit(lines, i):
            skip_counts["del_atexit"] += 1
            continue

        # Skip intentional comments on the pass line
        comment_part = (
            next_stripped[4:].strip().lower()
            if next_stripped.startswith("pass")
            else ""
        )
        if any(w in comment_part for w in INTENTIONAL_COMMENTS):
            skip_counts["comment"] += 1
            continue

        candidates.append(
            FixCandidate(
                file_path=file_path,
                rel_path=str(file_path.relative_to(SRC_DIR)).replace("\\", "/"),
                line_no=i + 1,  # 1-based
                indent=m.group(1),
                pass_line=next_line,
                pass_line_no=i + 2,  # 1-based
            )
        )

    return candidates, skip_counts


def _has_import_logging(text: str) -> bool:
    """Check if the file imports `logging`."""
    return bool(re.search(r"^import logging\b", text, re.MULTILINE))


def _has_get_logger(text: str) -> bool:
    """Check if the file has `logger = logging.getLogger(__name__)`."""
    return bool(re.search(r"^\s*logger\s*=\s*logging\.getLogger\b", text, re.MULTILINE))


def _insert_import_logging(lines: list[str]) -> list[str]:
    """Insert `import logging` in the import block."""
    # Find the last import line
    last_import = -1
    for i, line in enumerate(lines):
        if re.match(r"^(import\s+\w+|from\s+\w+)", line):
            last_import = i

    if last_import >= 0:
        lines.insert(last_import + 1, "import logging")
    else:
        # No imports at all — insert at top
        lines.insert(0, "import logging")
        lines.insert(1, "")

    return lines


def _insert_logger_init(lines: list[str]) -> list[str]:
    """Insert `logger = logging.getLogger(__name__)` after imports."""
    # Find where imports end
    insert_at = 0
    for i, line in enumerate(lines):
        if re.match(r"^(import\s+\w+|from\s+\w+)", line):
            insert_at = i + 1

    # Skip blank lines after import
    while insert_at < len(lines) and lines[insert_at].strip() == "":
        insert_at += 1

    lines.insert(insert_at, "")
    lines.insert(insert_at, "logger = logging.getLogger(__name__)")
    lines.insert(insert_at, "")

    return lines


def apply_fix(
    file_path: pathlib.Path, candidates: list[FixCandidate], dry_run: bool
) -> bool:
    """Apply the fix to a file. Returns True if changes were made."""
    text = file_path.read_text(encoding="utf-8")
    lines = text.split("\n")

    needs_log = not _has_import_logging(text)
    needs_logger = not _has_get_logger(text)

    # Work backwards to not mess up line numbers
    for cand in sorted(candidates, key=lambda c: c.pass_line_no, reverse=True):
        old_pass = lines[cand.pass_line_no - 1]
        # Extract the exact indent from the original pass line (NOT the except line)
        pass_indent = old_pass[: len(old_pass) - len(old_pass.lstrip())]
        new_pass = pass_indent + 'logger.debug("Non-critical error", exc_info=True)'
        # Preserve inline comments
        inline = old_pass.strip()[4:].strip()  # after "pass"
        if inline.startswith("#"):
            new_pass += "  " + inline
        lines[cand.pass_line_no - 1] = new_pass

    # Add imports if needed
    if needs_log:
        lines = _insert_import_logging(lines)
    if needs_logger:
        lines = _insert_logger_init(lines)

    new_text = "\n".join(lines)

    if not dry_run:
        file_path.write_text(new_text, encoding="utf-8")

    return True


def scan_and_fix(dry_run: bool = True, verbose: bool = False) -> Stats:
    """Main entry point: scan all .py files and apply fixes."""
    stats = Stats()

    # Group candidates by file
    files_candidates: dict[pathlib.Path, list[FixCandidate]] = {}

    for py_file in sorted(SRC_DIR.rglob("*.py")):
        stats.scanned += 1

        rel = str(py_file.relative_to(SRC_DIR)).replace("\\", "/")

        # Protected files
        if rel in PROTECTED_FILES:
            cands, skips = find_candidates(py_file)
            if cands:
                stats.skipped_protected += len(cands)
                if verbose:
                    for c in cands:
                        print(f"  SKIP (protected): {rel}:{c.line_no}")
            stats.skipped_comment += skips["comment"]
            stats.skipped_noqa += skips["noqa"]
            stats.skipped_finally += skips["finally"]
            stats.skipped_del_atexit += skips["del_atexit"]
            continue

        cands, skips = find_candidates(py_file)
        stats.skipped_comment += skips["comment"]
        stats.skipped_noqa += skips["noqa"]
        stats.skipped_finally += skips["finally"]
        stats.skipped_del_atexit += skips["del_atexit"]
        if cands:
            files_candidates[py_file] = cands

    # Process files
    for file_path, candidates in files_candidates.items():
        rel = str(file_path.relative_to(SRC_DIR)).replace("\\", "/")
        for c in candidates:
            print(
                f"  {'[DRY-RUN]' if dry_run else '[FIX]'} {rel}:{c.line_no} - except Exception -> pass"
            )

        if not dry_run:
            apply_fix(file_path, candidates, dry_run=False)

        stats.fixed += len(candidates)
        stats.files_changed += 1

    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Fix except Exception: pass patterns")
    parser.add_argument(
        "--dry-run", action="store_true", help="Only count, do not modify files"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show details for skipped lines"
    )
    args = parser.parse_args()

    print(f"Scanning {SRC_DIR} ...")
    stats = scan_and_fix(dry_run=args.dry_run, verbose=args.verbose)

    print()
    print("=" * 50)
    print(f"  Files scanned:      {stats.scanned}")
    print(f"  Skipped (protected): {stats.skipped_protected}")
    print(f"  Skipped (comment):   {stats.skipped_comment}")
    print(f"  Skipped (noqa):      {stats.skipped_noqa}")
    print(f"  Skipped (finally):   {stats.skipped_finally}")
    print(f"  Skipped (del/atexit):{stats.skipped_del_atexit}")
    print(f"  FIXES APPLIED:       {stats.fixed}")
    print(f"  Files changed:       {stats.files_changed}")
    print("=" * 50)

    if args.dry_run:
        print("\n[Dry-run mode -- no files were changed.]")
        print("Run without --dry-run to apply changes.")


if __name__ == "__main__":
    main()
