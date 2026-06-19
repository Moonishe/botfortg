"""mcp_file_analyzer tool — registered via @tool decorator.

Analyze various file formats on the local filesystem.

Actions:
- **read** — return first 2000 characters of a text file.
- **analyze** — detect format by extension and return parsed structure.
- **stats** — return lines, words, chars, and size for a file.

Safety:
    Path validation via ``mcp_tools._safe_resolve()`` — symlink-protected,
    denied-prefix/suffix checked, restricted to ``data/`` directory.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from xml.parsers.expat import ExpatError

_FATAL_EXCEPTIONS: tuple[type[BaseException], ...] = (
    asyncio.CancelledError,
    KeyboardInterrupt,
    SystemExit,
)

from src.core.actions.mcp_tools import _safe_resolve
from src.core.actions.tool_registry import tool

logger = logging.getLogger(__name__)


# ── Supported extensions ──────────────────────────────────────────────────

_TEXT_EXTENSIONS = frozenset({".txt", ".md", ".log"})
_STRUCTURED_EXTENSIONS = frozenset({".json", ".csv", ".yaml", ".yml", ".xml"})
# Явно запрещённые расширения — защита от чтения исходного кода и конфигов
# (belt-and-suspenders: _safe_resolve уже ограничивает data_dir, но
#  дополнительно блокируем чувствительные типы файлов)
_DENIED_EXTENSIONS = frozenset({".py", ".pyc", ".env", ".pem", ".key", ".crt"})

_READ_CHARS_LIMIT = 2000


# ══════════════════════════════════════════════════════════════════════════
# Tool: mcp_file_analyzer
# ══════════════════════════════════════════════════════════════════════════


@tool(
    name="mcp_file_analyzer",
    description=(
        "Read, analyze, or get stats for files on the local filesystem. "
        "Supports three actions:\n"
        "- 'read' — return first 2000 characters of a text file.\n"
        "- 'analyze' — detect format by extension and return parsed structure.\n"
        "- 'stats' — return line/word/char/size counts.\n"
        "Supported formats: .txt, .md, .log, .json, .csv, .yaml/.yml, .xml. "
        "Path is restricted to data/ directory."
    ),
    category="utility",
    risk="low",
    params={
        "action": "str — 'read', 'analyze', or 'stats'",
        "path": "str — relative path to the file (under data/)",
    },
)
async def mcp_file_analyzer(
    action: str,
    path: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    """File analysis tool.

    Args:
        action: ``"read"``, ``"analyze"``, or ``"stats"``.
        path: File path (relative to project root, restricted to ``data/``).

    Returns:
        A dict with the result data or an ``"error"`` key on failure.
    """
    try:
        # Validate path first — offload to thread (resolve may access filesystem)
        resolved = await asyncio.to_thread(_safe_resolve, path)
        if resolved is None:
            return {
                "error": (
                    f"Path {path!r} is outside allowed directories or contains '..'"
                )
            }
        if not await asyncio.to_thread(resolved.is_file):
            return {"error": f"Path {path!r} is not a file"}

        # Защита: явно запрещаем чувствительные расширения (.py, .env, ключи)
        if resolved.suffix.lower() in _DENIED_EXTENSIONS:
            return {
                "error": (
                    f"Files with extension {resolved.suffix!r} are not accessible "
                    f"for security reasons"
                )
            }

        if action == "read":
            return await _file_read(resolved)
        elif action == "analyze":
            return await _file_analyze(resolved)
        elif action == "stats":
            return await _file_stats(resolved)
        else:
            return {
                "error": (
                    f"Unknown action {action!r}. Valid actions: read, analyze, stats"
                )
            }
    except _FATAL_EXCEPTIONS:
        raise
    except Exception as exc:
        logger.exception("mcp_file_analyzer(%r, path=%r) failed", action, path)
        return {"error": str(exc)}


# ══════════════════════════════════════════════════════════════════════════
# Action implementations
# ══════════════════════════════════════════════════════════════════════════


async def _file_read(resolved: Path) -> dict[str, Any]:
    """Read first 2000 characters of a text file."""
    if (
        resolved.suffix.lower() not in _TEXT_EXTENSIONS
        and resolved.suffix.lower() not in _STRUCTURED_EXTENSIONS
    ):
        return {"error": f"Unsupported file extension: {resolved.suffix}"}

    try:
        text = await asyncio.to_thread(
            resolved.read_text, encoding="utf-8", errors="replace"
        )
    except PermissionError:
        return {"error": f"Permission denied: {resolved}"}
    except OSError as exc:
        return {"error": f"Cannot read file: {exc}"}

    total_len = len(text)
    content = text[:_READ_CHARS_LIMIT]

    return {
        "ok": True,
        "path": str(resolved),
        "content": content,
        "truncated": total_len > _READ_CHARS_LIMIT,
        "total_chars": total_len,
    }


async def _file_analyze(resolved: Path) -> dict[str, Any]:
    """Detect format by extension and return parsed structure."""
    ext = resolved.suffix.lower()

    if ext in _TEXT_EXTENSIONS:
        # Plain text — just read first 2000 chars
        try:
            text = await asyncio.to_thread(
                resolved.read_text, encoding="utf-8", errors="replace"
            )
        except PermissionError:
            return {"error": f"Permission denied: {resolved}"}
        except OSError as exc:
            return {"error": f"Cannot read file: {exc}"}

        return {
            "ok": True,
            "type": "text",
            "path": str(resolved),
            "content": text[:_READ_CHARS_LIMIT],
            "truncated": len(text) > _READ_CHARS_LIMIT,
        }

    elif ext == ".json":
        return await asyncio.to_thread(_analyze_json, resolved)
    elif ext == ".csv":
        return await asyncio.to_thread(_analyze_csv, resolved)
    elif ext in (".yaml", ".yml"):
        return await asyncio.to_thread(_analyze_yaml, resolved)
    elif ext == ".xml":
        return await asyncio.to_thread(_analyze_xml, resolved)
    else:
        # Unknown format — return size info
        try:
            stat_result = await asyncio.to_thread(resolved.stat)
            size = stat_result.st_size
        except OSError:
            size = -1
        return {
            "ok": True,
            "type": "unknown",
            "path": str(resolved),
            "size_bytes": size,
        }


async def _file_stats(resolved: Path) -> dict[str, Any]:
    """Return lines, words, chars, and size for a file."""
    try:
        stat_result = await asyncio.to_thread(resolved.stat)
        text = await asyncio.to_thread(
            resolved.read_text, encoding="utf-8", errors="replace"
        )
    except PermissionError:
        return {"error": f"Permission denied: {resolved}"}
    except OSError as exc:
        return {"error": f"Cannot read file: {exc}"}
    except UnicodeDecodeError:
        # Binary file — only size is available
        try:
            stat_result = await asyncio.to_thread(resolved.stat)
            size = stat_result.st_size
        except OSError:
            size = -1
        return {
            "ok": True,
            "path": str(resolved),
            "size_bytes": size,
            "note": "binary file — text stats unavailable",
        }

    lines = text.splitlines()
    words = len(text.split())
    chars = len(text)

    return {
        "ok": True,
        "path": str(resolved),
        "lines": len(lines),
        "words": words,
        "chars": chars,
        "size_bytes": stat_result.st_size,
    }


# ══════════════════════════════════════════════════════════════════════════
# Format-specific analyzers
# ══════════════════════════════════════════════════════════════════════════


def _analyze_json(resolved: Path) -> dict[str, Any]:
    """Parse and summarise a JSON file."""
    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"error": f"Malformed JSON: {exc}"}
    except PermissionError:
        return {"error": f"Permission denied: {resolved}"}
    except OSError as exc:
        return {"error": f"Cannot read file: {exc}"}

    return _summarize_json(data)


def _summarize_json(data: Any) -> dict[str, Any]:
    """Build a summary dict for a parsed JSON value."""
    if isinstance(data, dict):
        keys = list(data.keys())
        count = len(keys)
        sample = {k: data[k] for k in keys[:5]}
        return {
            "ok": True,
            "type": "json",
            "keys": keys[:20],
            "count": count,
            "sample": sample,
        }
    elif isinstance(data, list):
        count = len(data)
        sample = data[:5]
        return {
            "ok": True,
            "type": "json",
            "kind": "array",
            "count": count,
            "sample": sample,
        }
    else:
        return {
            "ok": True,
            "type": "json",
            "kind": "scalar",
            "value": data,
        }


def _analyze_csv(resolved: Path) -> dict[str, Any]:
    """Parse and summarise a CSV file."""
    try:
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except PermissionError:
        return {"error": f"Permission denied: {resolved}"}
    except OSError as exc:
        return {"error": f"Cannot read file: {exc}"}

    try:
        reader = csv.DictReader(io.StringIO(text))
        columns = reader.fieldnames or []
        rows: list[dict[str, str]] = []
        total_rows = 0
        for row in reader:
            total_rows += 1
            if len(rows) < 5:
                rows.append(row)
    except csv.Error as exc:
        return {"error": f"Malformed CSV: {exc}"}

    return {
        "ok": True,
        "type": "csv",
        "columns": columns,
        "rows": total_rows,
        "sample": rows,
    }


def _analyze_yaml(resolved: Path) -> dict[str, Any]:
    """Parse and summarise a YAML file."""
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return {
            "error": (
                "PyYAML is required to parse .yaml files. Install: pip install pyyaml"
            )
        }

    try:
        data = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return {"error": f"Malformed YAML: {exc}"}
    except PermissionError:
        return {"error": f"Permission denied: {resolved}"}
    except OSError as exc:
        return {"error": f"Cannot read file: {exc}"}

    if data is None:
        return {"ok": True, "type": "yaml", "note": "empty document"}

    return _summarize_json(data)  # JSON and YAML have compatible structures


def _analyze_xml(resolved: Path) -> dict[str, Any]:
    """Parse and summarise an XML file."""
    try:
        tree = ET.parse(resolved)
        root = tree.getroot()
    except (ET.ParseError, ExpatError) as exc:
        return {"error": f"Malformed XML: {exc}"}
    except PermissionError:
        return {"error": f"Permission denied: {resolved}"}
    except OSError as exc:
        return {"error": f"Cannot read file: {exc}"}

    # Collect direct child tags
    child_tags: list[str] = []
    seen: set[str] = set()
    for child in root:
        tag = child.tag
        if tag not in seen:
            child_tags.append(tag)
            seen.add(tag)

    return {
        "ok": True,
        "type": "xml",
        "root_tag": root.tag,
        "child_tags": child_tags[:20],
    }
