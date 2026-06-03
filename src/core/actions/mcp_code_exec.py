"""MCP Tool: безопасное выполнение Python-кода в изолированном subprocess."""

import asyncio
import logging
from typing import Any
import ast

from src.core.actions.tool_registry import tool

logger = logging.getLogger(__name__)


# ── AST sandbox validation ────────────────────────────────────────────
# Before executing code, parse and reject sandbox escape attempts

_SANDBOX_BLACKLIST = frozenset(
    {
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
        "exec",
        "eval",
        "compile",
        "open",
        "input",
        "breakpoint",
        "globals",
        "locals",
        "vars",
        "dir",
        "getattr",
        "setattr",
        "delattr",
        "type",
        "object",
    }
)

_SANDBOX_ALLOWED_NODES = frozenset(
    {
        ast.Expression,
        ast.Call,
        ast.Name,
        ast.Constant,
        ast.Load,
        ast.BinOp,
        ast.UnaryOp,
        ast.Compare,
        ast.BoolOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Mod,
        ast.Pow,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.And,
        ast.Or,
        ast.Not,
        ast.Attribute,
        ast.Subscript,
        ast.Index,
        ast.Slice,
        ast.Tuple,
        ast.List,
        ast.Set,
        ast.Dict,
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
        ast.comprehension,
        ast.IfExp,
        ast.JoinedStr,
        ast.FormattedValue,
    }
)


def _check_sandbox_safety(code: str) -> str | None:
    """Parse code AST and check for sandbox escape attempts.
    Returns error message if unsafe, None if safe."""
    try:
        tree = ast.parse(code, mode="eval")
    except SyntaxError:
        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError as e:
            return f"Syntax error: {e}"

    for node in ast.walk(tree):
        # Check name blacklist
        if isinstance(node, ast.Name) and node.id in _SANDBOX_BLACKLIST:
            return f"Access to '{node.id}' is not allowed in sandbox"

        # Check attribute blacklist
        if isinstance(node, ast.Attribute) and node.attr in _SANDBOX_BLACKLIST:
            return f"Access to attribute '.{node.attr}' is not allowed in sandbox"

    return None


# Запрещённые модули и builtins
_DISALLOWED_IMPORTS = {
    "os",
    "subprocess",
    "sys",
    "shutil",
    "socket",
    "requests",
    "httpx",
    "urllib",
    "http",
    "ftplib",
    "telnetlib",
    "smtplib",
    "imaplib",
    "pathlib",
    "glob",
    "fnmatch",
    "importlib",
    "__import__",
    "open",
    "exec",
    "eval",
    "compile",
    "execfile",
    "ctypes",
    "multiprocessing",
    "threading",
    "signal",
}

# Места для подстановки: __DISALLOWED__ — repr(list) запрещённого,
# __USER_CODE__ — индентированный код пользователя.
_WRAPPER_TEMPLATE = """\
import builtins
import sys

_SAFE_BUILTINS = {
    name: getattr(builtins, name)
    for name in {
        "abs", "all", "any", "bin", "bool", "bytes", "chr", "complex",
        "dict", "divmod", "enumerate", "filter", "float", "format", "frozenset",
        "hex", "int", "isinstance", "issubclass", "iter", "len", "list",
        "map", "max", "min", "oct", "ord", "pow", "print", "range",
        "repr", "reversed", "round", "set", "slice", "sorted", "str",
        "sum", "tuple", "type", "zip", "True", "False", "None", "Exception",
        "ValueError", "TypeError", "KeyError", "IndexError", "StopIteration",
        "ZeroDivisionError", "math", "json", "datetime", "collections",
        "itertools", "functools", "random", "statistics", "re", "hashlib",
        "base64", "textwrap", "string", "decimal", "fractions", "numbers",
        "csv", "io", "dataclasses",
    } if hasattr(builtins, name)
}

for name in _SAFE_BUILTINS:
    setattr(builtins, name, _SAFE_BUILTINS[name])

_DISALLOWED = __DISALLOWED__
for name in _DISALLOWED:
    if name in dir(builtins) and name not in _SAFE_BUILTINS:
        setattr(builtins, name, None)

_original_import = builtins.__import__


def _safe_import(name, *args, **kwargs):
    if name.split(".")[0] in _DISALLOWED:
        raise ImportError(f"Module '{name}' is not allowed in sandbox")
    return _original_import(name, *args, **kwargs)


builtins.__import__ = _safe_import

# Запрещаем open()
builtins.open = None

# Сохраняем stderr до удаления sys
_stderr = sys.stderr

# Скрываем внутренние переменные от пользовательского кода
del _original_import, _safe_import, _SAFE_BUILTINS, _DISALLOWED, builtins, sys

# Выполняем код пользователя
try:
__USER_CODE__
except Exception as e:
    print(f"Error: {type(e).__name__}: {e}", file=_stderr)
"""


@tool(
    name="code_exec",
    description=(
        "Выполняет Python-код в изолированной песочнице и возвращает результат. "
        "Можно использовать для вычислений, обработки данных, генерации текста. "
        "Доступны: math, json, datetime, collections, itertools, random, statistics, re, csv."
    ),
    category="utility",
    risk="medium",
    params={
        "code": "str — Python-код для выполнения. Вывод через print().",
        "timeout": "int — таймаут в секундах (1-30, по умолчанию 10)",
    },
)
async def code_exec(
    code: str = "",
    timeout: int = 10,
    **kwargs: Any,
) -> dict[str, Any]:
    if not code:
        return {"error": "code обязателен"}

    timeout = max(1, min(30, timeout))

    # AST-проверка кода на sandbox-escape
    err = _check_sandbox_safety(code)
    if err:
        return {"error": err}

    # Форматируем код с отступами (внутри try-блока)
    indented = "\n".join(f"    {line}" for line in code.split("\n"))

    # Подставляем запрещённые импорты и код пользователя в шаблон
    wrapper = _WRAPPER_TEMPLATE.replace(
        "__DISALLOWED__", repr(list(_DISALLOWED_IMPORTS))
    ).replace("__USER_CODE__", indented)

    try:
        # Запускаем в subprocess с ограничениями
        proc = await asyncio.create_subprocess_exec(
            "python",
            "-c",
            wrapper,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"error": f"Превышен таймаут ({timeout}с)", "output": ""}

        output = stdout.decode("utf-8", errors="replace").strip()
        error = stderr.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0 or error:
            return {
                "ok": False,
                "output": output[:2000] if output else "",
                "error": error[:1000] if error else f"Exit code {proc.returncode}",
            }

        return {
            "ok": True,
            "output": output[:2000],
        }

    except FileNotFoundError:
        return {"error": "Python не найден. Убедись что python в PATH."}
    except Exception as e:
        return {"error": str(e)[:300]}
