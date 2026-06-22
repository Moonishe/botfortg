"""SDD (Structured Data Dispatcher) — safe Python sandbox for LLM-generated code.

Allows an LLM to write and execute short Python scripts for batch operations
(e.g. marking all reminders as done, bulk-tagging facts, mass-updating data).
Provides 10-50x speed improvement over sequential LLM calls by running
user-generated code in a restricted AST-level sandbox.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import json
import logging
import marshal
import subprocess
import sys
from typing import Any

from src.config import settings
from src.core.actions.tool_registry import tool

logger = logging.getLogger(__name__)

# ── AST whitelist ─────────────────────────────────────────────────────────
# Only these node types are allowed in submitted code.

_ALLOWED_NODES: set[type[ast.AST]] = {
    # Top-level
    ast.Module,
    ast.Expr,
    ast.Pass,
    # Assignment
    ast.Assign,
    ast.AugAssign,
    ast.AnnAssign,
    # Primitives / literals
    ast.Name,
    ast.Constant,
    ast.Attribute,
    ast.Subscript,
    ast.List,
    ast.Dict,
    ast.Tuple,
    ast.Set,
    ast.IfExp,  # ternary: a if cond else b
    ast.Starred,  # *args unpacking
    ast.Slice,  # slicing: x[1:2]
    # Calls
    ast.Call,
    ast.keyword,
    # Operators
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    # Control flow
    ast.If,
    ast.For,
    ast.Break,
    ast.Continue,
    ast.Return,
    ast.Delete,
    # Expression context
    ast.Load,
    ast.Store,
    ast.Del,
    # Arithmetic operators
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    # Comparison operators
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Is,
    ast.IsNot,
    ast.In,
    ast.NotIn,
    # Boolean operators
    ast.And,
    ast.Or,
    ast.Not,
}

# Names that are strictly forbidden in submitted code (both as Name and as
# Attribute targets).
_BLACKLIST: set[str] = {
    "__import__",
    "__class__",
    "__bases__",
    "__subclasses__",
    "__mro__",
    "__init__",
    "__globals__",
    "__code__",
    "__builtins__",
    "__getattribute__",
    "__getattr__",
    "__setattr__",
    "__delattr__",
    "__dir__",
    "__format__",
    "__reduce__",
    "__reduce_ex__",
    "__sizeof__",
    "eval",
    "exec",
    "compile",
    "open",
    "input",
    "breakpoint",
    "getattr",
    "setattr",
    "delattr",
    "type",
    "object",
    "memoryview",
    "bytearray",
}


# ── Validation helpers ────────────────────────────────────────────────────


# Keys that MUST NOT be passed into the sandbox namespace via **safe_kwargs,
# as they would override critical safety internals.
# ponytail: frozenset, upgrade to enum if namespacing gets more complex.
_RESERVED_NAMESPACE_KEYS: frozenset[str] = frozenset(
    {
        "__builtins__",
        "kwargs",
        "safe_builtins",
        "safe_kwargs",
        "namespace",
        "output_buffer",
        "_sandbox_print",
        "code",
        "result",
        "session",
        "user",
        "provider",
        "userbot_manager",
        "owner",
        "bot",
    }
)


def _is_safe(node: ast.AST) -> bool:
    """Recursively check that all nodes in the AST are allowed.

    Returns ``True`` if the entire tree passes the whitelist and blacklist
    checks.
    """
    if type(node) not in _ALLOWED_NODES:
        return False
    # Check blacklisted names
    if isinstance(node, ast.Name) and node.id in _BLACKLIST:
        return False
    # Check blacklisted attributes
    if isinstance(node, ast.Attribute) and node.attr in _BLACKLIST:
        return False
    for child in ast.iter_child_nodes(node):
        if not _is_safe(child):
            return False
    return True


# ── Execution ─────────────────────────────────────────────────────────────


async def execute_code(code: str, **kwargs: Any) -> dict[str, Any]:
    """Safely execute LLM-generated Python code in a restricted sandbox.

    The submitted *code* is parsed, validated against an AST whitelist, and
    executed in a controlled namespace with a limited set of builtins.

    **Available builtins:** ``print``, ``len``, ``range``, ``int``, ``str``,
    ``float``, ``bool``, ``list``, ``dict``, ``set``, ``tuple``, ``zip``,
    ``enumerate``, ``sorted``, ``min``, ``max``, ``sum``, ``any``, ``all``,
    ``isinstance``.

    **Available via kwargs:** any keyword arguments passed by the caller
    (e.g. ``session``, ``user``, ``provider``, ``test_data``).

    **Convention:** the code may set a ``_result`` variable in the global
    namespace; its value will be returned in the ``"result"`` field of the
    response dict.

    Args:
        code: Valid Python source code (no imports, no ``eval``/``exec``).
        **kwargs: Names to inject into the execution namespace.

    Returns:
        A dict with keys:
        - ``"output"``: captured ``print()`` output (truncated to 5000 chars).
        - ``"result"``: string representation of ``_result`` (truncated to
          2000 chars), or ``None`` if not set.
        - ``"error"``: error message on failure, or ``None`` on success.
    """
    if not code or not isinstance(code, str) or not code.strip():
        return {
            "output": "",
            "result": None,
            "error": "code parameter is required",
        }

    # 1. Parse and validate AST — with timeout to prevent slow-parse DoS.
    # ponytail: to_thread + wait_for, upgrade to dedicated process if parse bombs grow.
    try:
        tree = await asyncio.wait_for(
            asyncio.to_thread(ast.parse, code, mode="exec"),
            timeout=5,
        )
    except asyncio.TimeoutError:
        return {"output": "", "result": None, "error": "AST parse timeout (5s)"}
    except SyntaxError as e:
        return {"output": "", "result": None, "error": f"Syntax error: {e}"}

    try:
        is_safe = await asyncio.wait_for(
            asyncio.to_thread(_is_safe, tree),
            timeout=5,
        )
    except asyncio.TimeoutError:
        return {"output": "", "result": None, "error": "AST validation timeout (5s)"}

    if not is_safe:
        # Pinpoint the first disallowed node for a helpful error message
        for node in ast.walk(tree):
            if type(node) not in _ALLOWED_NODES:
                return {
                    "output": "",
                    "result": None,
                    "error": f"Unsafe operation: {type(node).__name__} is not allowed",
                }
        return {
            "output": "",
            "result": None,
            "error": "Code contains unsafe operations (blacklisted names)",
        }

    # 2. Sanitize kwargs — never pass DB/callbacks to sandbox.
    # Only JSON-serializable values survive (prevents code injection via repr).
    def _is_json_serializable(v: Any) -> bool:
        try:
            json.dumps(v)
        except (TypeError, ValueError):
            return False
        # Guard: json.dumps(float('inf')) → 'Infinity' which is NOT valid JSON
        # and json.loads will reject it.  Reject inf/-inf/nan explicitly.
        if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
            return False
        return True

    def _safe_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
        """Filter kwargs for safe passage to sandbox namespace."""
        return {
            k: v
            for k, v in kwargs.items()
            if k not in _RESERVED_NAMESPACE_KEYS and _is_json_serializable(v)
        }

    # 3. Execute in isolated subprocess (with timeout)
    # Prior design used exec() in a thread pool, which leaked threads
    # on timeout — asyncio.wait_for cancelled the Future but the thread
    # kept running.  Subprocess isolation guarantees the process and
    # all its resources are killed after timeout.

    # Serialize code via marshal+base64 — subprocess script reads and executes.
    # Keeps AST validation in main process, only code execution in subprocess.
    code_bytes = marshal.dumps(compile(tree, "<sdd>", "exec"))
    safe_kwargs_json = json.dumps(_safe_kwargs(kwargs), ensure_ascii=True)
    script = (
        "import marshal, base64, json, sys, io\n"
        "code = marshal.loads(base64.b64decode("
        + json.dumps(base64.b64encode(code_bytes).decode(), ensure_ascii=True)
        + "))\n"
        "safe_kwargs = json.loads("
        + json.dumps(safe_kwargs_json, ensure_ascii=True)
        + ")\n"
        "safe_builtins = {k: getattr(__builtins__, k) for k in ['print','len','range','int','str','float','bool','list','dict','set','tuple','zip','enumerate','sorted','min','max','sum','any','all','isinstance']}\n"
        "safe_builtins.update({'True': True, 'False': False, 'None': None})\n"
        "namespace = {'__builtins__': safe_builtins, 'kwargs': safe_kwargs, **safe_kwargs}\n"
        "output_buffer = io.StringIO()\n"
        "def _sandbox_print(*args, **kw):\n"
        "    print(*args, file=output_buffer, **kw)\n"
        "safe_builtins['print'] = _sandbox_print\n"
        "try:\n"
        "    exec(code, namespace)\n"
        "    result = {'output': output_buffer.getvalue().strip()[:5000], 'result': str(namespace.get('_result', ''))[:2000], 'error': None}\n"
        "except Exception as e:\n"
        "    result = {'output': output_buffer.getvalue().strip()[:2000], 'result': None, 'error': str(e)[:500]}\n"
        "sys.stdout.write(json.dumps(result))\n"
    )

    # Docker sandbox — execute inside container instead of host subprocess.
    if settings.sandbox_enabled:
        from src.core.sandbox import SandboxManager

        manager = SandboxManager(settings)
        try:
            sandbox_result = await manager.exec(
                ["python", "-c", script],
                timeout=5,
            )
            sandbox_stdout = sandbox_result.get("stdout", "")
            try:
                return json.loads(sandbox_stdout)
            except json.JSONDecodeError:
                return {
                    "error": f"execution failed: {sandbox_result.get('stderr', '')[:500]}",
                    "output": "",
                    "result": None,
                }
        except RuntimeError as exc:
            return {
                "error": f"sandbox error: {exc}",
                "output": "",
                "result": None,
            }
        finally:
            await manager.cleanup()

    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=5,
        )
        result = json.loads(proc.stdout)
        return result
    except subprocess.TimeoutExpired:
        return {
            "error": "execution timed out (5s limit)",
            "output": "",
            "result": None,
        }
    except json.JSONDecodeError:
        return {
            "error": f"execution failed: {proc.stderr[:500] if proc else 'unknown'}",
            "output": "",
            "result": None,
        }


# ── @tool registration ────────────────────────────────────────────────────


@tool(
    name="execute_code",
    description=(
        "Выполняет безопасный Python-код для пакетных операций. "
        "Используй для: отметить несколько напоминаний done, "
        "проставить теги фактам, массово обновить данные. "
        "НЕ используй для единичных операций. "
        "(нет доступа к БД/сессии — для массовых вычислений, не для запросов данных)"
    ),
    category="system",
    risk="critical",
    requires_confirmation=True,
    params={
        "code": "str — валидный Python-код (безопасный, без импортов, без eval/exec)"
    },
    output_schema={
        "type": "object",
        "properties": {
            "result": {"description": "Value of _result variable from executed code"},
            "output": {"type": "string", "description": "Captured print() output"},
            "error": {
                "type": "string",
                "description": "Execution error or sandbox rejection",
            },
        },
        "required": [],
    },
)
async def _execute_code_tool(code: str, **kwargs: Any) -> dict[str, Any]:
    """Tool wrapper — delegates to the sandbox ``execute_code``."""
    return await execute_code(code, **kwargs)
