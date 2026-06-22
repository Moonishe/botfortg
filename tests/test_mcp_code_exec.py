"""Minimal sandbox security tests for mcp_code_exec fixes.

Validates:
1. CRITICAL: `_type` captured before `type` nullified
2. HIGH: traceback/frame attributes blocked in AST
3. HIGH: getattr/hasattr nullified defense-in-depth
4. MEDIUM: tuple available in safe builtins
"""

import ast

import pytest

from src.core.actions.mcp_code_exec import (
    _check_sandbox_safety,
    _SANDBOX_BLACKLIST,
    _WRAPPER_TEMPLATE,
    _DISALLOWED_IMPORTS,
)


# ── CRITICAL fix: _type capture before type nullify ──────────────────


def test_type_captured_before_nullify():
    """_type = type must appear before type is set to None in builtins."""
    # _type capture must be before the del line that removes `builtins`
    # and before the user code block
    assert "_type = type" in _WRAPPER_TEMPLATE, (
        "_type = type must be captured before type is nullified"
    )
    # _type is deleted before user code runs — exception handler uses e.__class__.__name__
    assert "e.__class__.__name__" in _WRAPPER_TEMPLATE, (
        "Exception handler must use e.__class__.__name__ (not _type, which is deleted)"
    )
    # The bare f-string expression {type(e).__name__} must NOT appear
    assert "{type(e).__name__}" not in _WRAPPER_TEMPLATE, (
        "bare {type(e).__name__} f-string expr must not appear;"
        " type() is nullified in builtins"
    )
    # _type capture must precede BOTH the nullify loop AND the del builtins
    type_pos = _WRAPPER_TEMPLATE.index("_type = type")
    nullify_pos = _WRAPPER_TEMPLATE.index("for name in _DISALLOWED:")
    # _type must be in the del statement (not left accessible to user code)
    del_pos = _WRAPPER_TEMPLATE.index("del _original_import, _safe_import")
    assert (
        "_type"
        in _WRAPPER_TEMPLATE.split("del _original_import, _safe_import")[1].split("\n")[
            0
        ]
    ), "_type must be deleted (must appear in del statement)"
    assert type_pos < nullify_pos < del_pos, (
        "_type capture must be before nullify loop, which is before del builtins"
    )


# ── HIGH: traceback/frame attributes blocked ─────────────────────────

_TRACEBACK_BLACKLIST = [
    "__traceback__",
    "tb_frame",
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
    "cr_code",
    "cr_frame",
    "cr_globals",
]


@pytest.mark.parametrize("blocked_name", _TRACEBACK_BLACKLIST)
def test_traceback_attrs_in_blacklist(blocked_name):
    """Every traceback/frame/coroutine attr must be in _SANDBOX_BLACKLIST."""
    assert blocked_name in _SANDBOX_BLACKLIST, (
        f"{blocked_name} missing from _SANDBOX_BLACKLIST"
    )


@pytest.mark.parametrize(
    "code_snippet",
    [
        "e.__traceback__",
        "e.__traceback__.tb_frame",
        "e.__traceback__.tb_frame.f_globals",
        "e.__traceback__.tb_frame.f_builtins",
        "e.__traceback__.tb_frame.f_code",
        "e.__traceback__.tb_frame.f_locals",
        "e.__traceback__.tb_frame.f_back",
        "e.__traceback__.tb_lineno",
        "e.__traceback__.tb_next",
        "e.__traceback__.tb_frame.f_lasti",
        "e.__traceback__.tb_frame.f_lineno",
        "e.__traceback__.tb_frame.f_trace",
    ],
)
def test_traceback_chains_blocked_by_ast(code_snippet):
    """AST validator must reject traceback escape chains."""
    full_code = f"try:\n    1/0\nexcept Exception as e:\n    {code_snippet}\n"
    err = _check_sandbox_safety(full_code)
    assert err is not None, f"Expected block for: {code_snippet}"
    assert "not allowed" in err


# ── HIGH: getattr/hasattr blocked ────────────────────────────────────


@pytest.mark.parametrize("blocked_name", ["getattr", "setattr", "delattr", "hasattr"])
def test_reflection_builtins_in_blacklist(blocked_name):
    """getattr, setattr, delattr, hasattr must be in blacklist."""
    assert blocked_name in _SANDBOX_BLACKLIST, (
        f"{blocked_name} missing from _SANDBOX_BLACKLIST"
    )


@pytest.mark.parametrize(
    "code_snippet",
    [
        "getattr(x, '__class__')",
        "hasattr(x, '__bases__')",
        "setattr(x, '__class__', None)",
        "delattr(x, '__class__')",
        "getattr(getattr((), '__class__'), '__bases__')",
    ],
)
def test_reflection_blocked_by_ast(code_snippet):
    """AST validator must reject getattr/setattr/delattr/hasattr."""
    err = _check_sandbox_safety(code_snippet)
    assert err is not None, f"Expected block for: {code_snippet}"
    assert "not allowed" in err


# ── MEDIUM: tuple returned to safe builtins ──────────────────────────


def test_tuple_in_safe_builtins():
    """tuple must be in the _SAFE_BUILTINS set within _WRAPPER_TEMPLATE."""
    assert '"tuple"' in _WRAPPER_TEMPLATE or "'tuple'" in _WRAPPER_TEMPLATE, (
        "tuple must be in _SAFE_BUILTINS set in _WRAPPER_TEMPLATE"
    )


# ── Smoke: template can be formatted without syntax errors ───────────


def test_wrapper_template_is_valid_python():
    """Wrapper template with placeholder replaced must be valid Python AST."""
    wrapper = _WRAPPER_TEMPLATE.replace(
        "__DISALLOWED__", repr(tuple(_DISALLOWED_IMPORTS))
    ).replace("__USER_CODE__", "    x = 1 + 1\n    print(x)")
    try:
        ast.parse(wrapper)
    except SyntaxError as e:
        pytest.fail(f"Generated wrapper has syntax error: {e}")


@pytest.mark.asyncio
async def test_code_exec_simple_expression():
    """code_exec runs a simple expression successfully."""
    from src.core.actions.mcp_code_exec import code_exec

    result = await code_exec(code="print(2 + 2)", timeout=5)
    assert result.get("ok") is True, f"Expected ok=True, got: {result}"
    assert "4" in result.get("output", ""), f"Expected output '4', got: {result}"


@pytest.mark.asyncio
async def test_code_exec_exception_handling():
    """code_exec must report exception type name, not crash on type(e)."""
    from src.core.actions.mcp_code_exec import code_exec

    result = await code_exec(code="raise ValueError('test sandbox error')", timeout=5)
    # Should return ok=False with error containing ValueError
    assert result.get("ok") is False, f"Expected ok=False, got: {result}"
    error_msg = result.get("error", "")
    assert "ValueError" in error_msg, (
        f"Error must contain 'ValueError', got: {error_msg!r}"
    )
    # CRITICAL: must NOT contain NoneType (would mean type() returned None)
    assert "NoneType" not in error_msg, (
        f"type(e) returned None — _type capture failed. Error: {error_msg!r}"
    )


# ── HIGH: subscript bypass blocked ────────────────────────────────────


def test_subscript_bypass_blocked():
    """Subscript access to blacklisted attrs must be blocked."""
    code = 'obj.__dict__["__subclasses__"]'
    err = _check_sandbox_safety(code)
    assert err is not None, "Expected block for subscript bypass"
    assert "not allowed" in err


def test_subscript_bypass_blocked_simple():
    """Direct subscript with blacklisted string must be blocked."""
    code = 'd["__class__"]'
    err = _check_sandbox_safety(code)
    assert err is not None, "Expected block for d['__class__']"
    assert "not allowed" in err
