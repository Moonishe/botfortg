"""MCP Tool: безопасное выполнение Python-кода в изолированном subprocess."""

import asyncio
import logging
from typing import Any
import ast

from src.config import settings
from src.core.actions.tool_registry import tool
from src.core.infra.key_guard import safe_str

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
        "__closure__",
        "__builtins__",
        "__getattribute__",
        "__getattr__",
        "__setattr__",
        "__delattr__",
        "__dict__",
        "__dir__",
        "__format__",
        "__reduce__",
        "__reduce_ex__",
        "__sizeof__",
        "__traceback__",
        "tb_frame",
        "tb_lasti",
        "tb_lineno",
        "tb_next",
        "f_back",
        "f_builtins",
        "f_code",
        "f_globals",
        "f_lasti",
        "f_lineno",
        "f_locals",
        "f_trace",
        "f_trace_lines",
        "f_trace_opcodes",
        "cr_code",
        "cr_frame",
        "cr_globals",
        "gi_frame",
        "gi_code",
        "ag_frame",
        "ag_code",
        "co_consts",
        "co_names",
        "co_code",
        "co_filename",
        "co_varnames",
        "co_freevars",
        "co_cellvars",
        "cr_await",
        "cr_origin",
        "cr_running",
        "gi_running",
        "gi_yieldfrom",
        "ag_running",
        "ag_yieldfrom",
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
        "hasattr",
        "type",
        "object",
        "gc",
        "issubclass",
        "isinstance",
    }
)

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
    "io",
    "pickle",
    "marshal",
    "code",
    "codeop",
    "inspect",
    "traceback",
    "pdb",
    "runpy",
    "fcntl",
    "posix",
    "nt",
    "_thread",
    "pty",
    "atexit",
    "builtins",
    "faulthandler",
}


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

        # Check subscript with string constant key (blocks obj['__subclasses__'] bypass)
        if isinstance(node, ast.Subscript):
            slice_val = node.slice
            # Python 3.9+: slice is directly an ast.Constant
            if isinstance(slice_val, ast.Constant) and isinstance(slice_val.value, str):
                if slice_val.value in _SANDBOX_BLACKLIST:
                    return f"Subscript access to '{slice_val.value}' is not allowed in sandbox"
            # Handle ast.Index wrapper (removed in 3.9+, defense-in-depth for older Python)
            _Index = getattr(ast, "Index", None)
            if _Index is not None and isinstance(slice_val, _Index):
                inner = slice_val.value  # type: ignore[attr-defined]
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    if inner.value in _SANDBOX_BLACKLIST:
                        return f"Subscript access to '{inner.value}' is not allowed in sandbox"

        # Block disallowed imports at AST level (defense in depth)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                module_name = alias.name.split(".")[0]
                if module_name in _DISALLOWED_IMPORTS:
                    return f"Import of '{module_name}' is not allowed in sandbox"
            if (
                isinstance(node, ast.ImportFrom)
                and (node.module or "") in _DISALLOWED_IMPORTS
            ):
                return f"Import from '{node.module}' is not allowed in sandbox"

        # Block any call through gc (gc.get_objects(), gc.get_referents(), etc.)
        if isinstance(node, ast.Call):
            func = node.func
            # gc.something(...)
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "gc"
            ):
                return "Access to gc module is not allowed in sandbox"
            # gc(...)
            if isinstance(func, ast.Name) and func.id == "gc":
                return "Access to gc module is not allowed in sandbox"

    return None


# Места для подстановки: __DISALLOWED__ — repr(tuple) запрещённого (tuple —
# иммутабелен, чтобы пользовательский код не мог очистить блок-лист через
# builtins.__import__.__defaults__[0].clear()),
# __USER_CODE__ — индентированный код пользователя.
_WRAPPER_TEMPLATE = """\
import builtins
import sys

# Capture original type BEFORE any builtins are modified/nullified
_type = type

# ── RLIMITs: ограничение ресурсов подпроцесса (только Linux/macOS) ──
# Ограничиваем CPU, память и запрещаем fork для предотвращения DoS.
# На Windows resource отсутствует — ограничения не применяются.
try:
    import resource as _resource
    try:
        _resource.setrlimit(_resource.RLIMIT_CPU, (5, 5))           # 5 сек CPU
        _MB = 128 * 1024 * 1024
        _resource.setrlimit(_resource.RLIMIT_AS, (_MB, _MB))         # 128 MB
        _resource.setrlimit(_resource.RLIMIT_NPROC, (0, 0))         # запрет subprocess
    except (ValueError, _resource.error):
        pass  # RLIMITs недоступны на данной платформе
    del _resource
except ImportError:
    # Windows — модуль resource отсутствует.
    # RLIMITs не применяются; defence-in-depth обеспечивается через asyncio.wait_for timeout
    # (см. proc.communicate() с asyncio.wait_for в вызывающем коде).
    pass

_SAFE_BUILTINS = {
    name: getattr(builtins, name)
    for name in {
        "abs", "all", "any", "bin", "bool", "bytes", "chr", "complex",
        "dict", "divmod", "enumerate", "filter", "float", "format", "frozenset",
        "hex", "int", "iter", "len", "list",
        "map", "max", "min", "oct", "ord", "pow", "print", "range",
        "repr", "reversed", "round", "set", "slice", "sorted", "str",
        "sum", "tuple", "zip", "True", "False", "None", "Exception",
        "ValueError", "TypeError", "KeyError", "IndexError", "StopIteration",
        "ZeroDivisionError", "math", "json", "datetime", "collections",
        "itertools", "functools", "random", "statistics",
        "base64", "textwrap", "string", "decimal", "fractions", "numbers",
        "csv", "dataclasses",
    } if hasattr(builtins, name)
}

for name in _SAFE_BUILTINS:
    setattr(builtins, name, _SAFE_BUILTINS[name])

# Nullify dangerous builtins that could enable sandbox escape
# (defense-in-depth — also blocked at AST level by _SANDBOX_BLACKLIST)
_DISALLOWED = __DISALLOWED__
for name in _DISALLOWED:
    if name in dir(builtins) and name not in _SAFE_BUILTINS:
        setattr(builtins, name, None)

_original_import = builtins.__import__

# Capture _DISALLOWED in closure via default arg so it survives `del` below.
def _safe_import(name, *args, _blocked=_DISALLOWED, **kwargs):
    if name.split(".")[0] in _blocked:
        raise ImportError(f"Module '{name}' is not allowed in sandbox")
    return _original_import(name, *args, **kwargs)


builtins.__import__ = _safe_import

# Запрещаем open()
builtins.open = None

# Сохраняем stderr до удаления sys
_stderr = sys.stderr

# Скрываем внутренние переменные от пользовательского кода
# ponytail: _DISALLOWED captured via default arg in _safe_import, safe to del here
del _original_import, _safe_import, _SAFE_BUILTINS, _DISALLOWED, builtins, sys, _type

# Выполняем код пользователя
try:
__USER_CODE__
except MemoryError:
    print("Error: MemoryError: превышен лимит памяти (128 MB)", file=_stderr)
except TimeoutError:
    print("Error: TimeoutError: превышен лимит CPU (5 сек)", file=_stderr)
except Exception as e:
    print(f"Error: {e.__class__.__name__}: {e}", file=_stderr)
"""


async def _run_code_in_sandbox(wrapper: str, timeout: int) -> dict[str, Any]:
    """Execute *wrapper* inside a Docker sandbox via :class:`SandboxManager`."""
    from src.core.sandbox import SandboxManager

    manager = SandboxManager(settings)
    try:
        result = await manager.exec(
            ["python", "-c", wrapper],
            timeout=timeout,
        )
        stdout = (result["stdout"] or "").strip()
        stderr = (result["stderr"] or "").strip()

        if result["returncode"] != 0 or stderr:
            return {
                "ok": False,
                "output": stdout[:2000] if stdout else "",
                "error": stderr[:1000]
                if stderr
                else f"Exit code {result['returncode']}",
            }

        return {
            "ok": True,
            "output": stdout[:2000],
        }
    except RuntimeError as exc:
        return {"error": safe_str(exc)[:300]}
    finally:
        await manager.cleanup()


@tool(
    name="code_exec",
    description=(
        "Выполняет Python-код в изолированной песочнице и возвращает результат. "
        "Можно использовать для вычислений, обработки данных, генерации текста. "
        "Доступны: math, json, datetime, collections, itertools, random, "
        "statistics, re, csv."
    ),
    category="utility",
    risk="critical",
    requires_confirmation=True,
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
        "__DISALLOWED__", repr(tuple(_DISALLOWED_IMPORTS))
    ).replace("__USER_CODE__", indented)

    # Запускаем в subprocess с ограничениями (или в Docker-песочнице)
    if settings.sandbox_enabled:
        return await _run_code_in_sandbox(wrapper, timeout)

    try:
        proc = await asyncio.create_subprocess_exec(
            "python",
            "-c",
            wrapper,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return {"error": "Python не найден. Убедись что python в PATH."}

    try:
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except TimeoutError:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except TimeoutError:
                proc.kill()  # escalate: SIGKILL after SIGTERM didn't work
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except TimeoutError:
                    pass  # process is zombie — let OS reap it
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

    except Exception as e:
        return {"error": safe_str(e)[:300]}
    finally:
        # Гарантированная очистка subprocess: kill если ещё жив
        # (покрывает CancelledError, decode-ошибки и прочие нештатные ситуации)
        try:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3.0)
                except TimeoutError:
                    proc.kill()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=5.0)
                    except TimeoutError:
                        pass  # zombie — let OS reap it
        except (ProcessLookupError, OSError):
            pass  # процесс уже завершился между проверкой и действием
