"""
tests/test_runner.py  —  Tests for runner.py
=============================================
We test compile_program() and run_binary() with a known-good, hand-written
C program rather than a generated one.  This keeps these tests independent
of the generator so a generator bug doesn't mask a runner bug.

Tests that require gcc or clang are skipped automatically when those
compilers are not on PATH.
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from runner import compile_program, run_binary, all_combinations

# ── a minimal, known-correct C program ───────────────────────────────────────
# This program always prints exactly "hello=42" and exits 0.
# It's the simplest possible program that exercises printf.

HELLO_C = """\
#include <stdio.h>
int main(void) {
    unsigned int hello = 42u;
    printf("hello=%u\\n", hello);
    return 0;
}
"""

# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def hello_c_file(tmp_path: Path) -> Path:
    """
    pytest fixture: write HELLO_C to a temp file and return its Path.
    `tmp_path` is a built-in pytest fixture that provides a fresh temp dir
    per test — no manual cleanup needed.
    """
    src = tmp_path / "hello.c"
    src.write_text(HELLO_C)
    return src


# ── all_combinations() ────────────────────────────────────────────────────────

def test_all_combinations_returns_list():
    """all_combinations() must return a non-empty list of tuples."""
    combos = all_combinations()
    assert isinstance(combos, list)
    assert len(combos) > 0


def test_all_combinations_are_tuples_of_two_strings():
    """Each combo must be a (str, str) tuple."""
    for item in all_combinations():
        assert isinstance(item, tuple) and len(item) == 2
        compiler, flag = item
        assert isinstance(compiler, str)
        assert isinstance(flag, str)


# ── compile_program() with gcc ────────────────────────────────────────────────

@pytest.mark.skipif(
    shutil.which("gcc") is None,
    reason="gcc not found on PATH.",
)
def test_compile_success_gcc(hello_c_file: Path):
    """gcc should compile HELLO_C without errors."""
    result = compile_program(hello_c_file, "gcc", "-O0")
    assert result.success is True,  f"Compile failed:\n{result.stderr}"
    assert result.binary_path is not None
    assert result.binary_path.exists()
    assert result.skipped is False


@pytest.mark.skipif(
    shutil.which("gcc") is None,
    reason="gcc not found on PATH.",
)
def test_compile_stderr_empty_on_success(hello_c_file: Path):
    """Clean code should produce no warnings (-Wall -Wextra)."""
    result = compile_program(hello_c_file, "gcc", "-O0")
    assert result.stderr.strip() == "", (
        f"Unexpected warnings:\n{result.stderr}"
    )


# ── compile_program() with clang ──────────────────────────────────────────────

@pytest.mark.skipif(
    shutil.which("clang") is None,
    reason="clang not found on PATH.",
)
def test_compile_success_clang(hello_c_file: Path):
    """clang should compile HELLO_C without errors."""
    result = compile_program(hello_c_file, "clang", "-O0")
    assert result.success is True, f"Compile failed:\n{result.stderr}"
    assert result.binary_path is not None


# ── compile_program() — missing compiler ─────────────────────────────────────

def test_compile_missing_compiler(hello_c_file: Path):
    """
    Passing a compiler name that doesn't exist on PATH should set
    skipped=True and success=False without raising an exception.
    """
    result = compile_program(hello_c_file, "this_compiler_does_not_exist_xyz", "-O0")
    assert result.skipped  is True
    assert result.success  is False
    assert result.binary_path is None


# ── compile_program() — invalid source ───────────────────────────────────────

@pytest.mark.skipif(
    shutil.which("gcc") is None,
    reason="gcc not found on PATH.",
)
def test_compile_failure_on_bad_source(tmp_path: Path):
    """Passing broken C should give success=False and non-empty stderr."""
    bad_c = tmp_path / "bad.c"
    bad_c.write_text("this is not valid C code !!!;\n")

    result = compile_program(bad_c, "gcc", "-O0")
    assert result.success is False
    assert result.stderr.strip() != "", "Expected error output for invalid C source."


# ── run_binary() ─────────────────────────────────────────────────────────────

@pytest.mark.skipif(
    shutil.which("gcc") is None,
    reason="gcc not found on PATH.",
)
def test_run_binary_correct_output(hello_c_file: Path):
    """Compiling and running HELLO_C must print 'hello=42'."""
    cr = compile_program(hello_c_file, "gcc", "-O0")
    assert cr.success, f"Compile step failed:\n{cr.stderr}"

    rr = run_binary(cr.binary_path)
    assert rr.timed_out  is False
    assert rr.exit_code  == 0
    assert rr.stdout     == "hello=42"   # runner.py strips trailing newline


@pytest.mark.skipif(
    shutil.which("gcc") is None,
    reason="gcc not found on PATH.",
)
def test_run_binary_exit_code(hello_c_file: Path):
    """A program that returns 0 must give exit_code == 0."""
    cr = compile_program(hello_c_file, "gcc", "-O0")
    rr = run_binary(cr.binary_path)
    assert rr.exit_code == 0
