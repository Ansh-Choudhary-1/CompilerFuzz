"""
tests/test_generator.py  —  Tests for generator.py
====================================================
What we verify:
  1. Determinism      — same seed → identical source code every time.
  2. Seed isolation   — different seeds → different programs.
  3. Structure        — the output is valid-looking C (has the key landmarks).
  4. No warnings      — gcc -Wall -Wextra -std=c11 compiles it cleanly
                        (zero warnings means the generator isn't creating UB
                        or sloppy code that a real compiler notices).
"""

import subprocess
import shutil
import tempfile
from pathlib import Path

import pytest

# We import from the parent package.
# pytest adds the workspace root to sys.path when run from there, so this works.
from generator import generate_c_program


# ── 1. Determinism ────────────────────────────────────────────────────────────

def test_same_seed_gives_same_output():
    """Calling generate_c_program with the same seed twice must return
    byte-for-byte identical strings."""
    seed = 42
    first  = generate_c_program(seed)
    second = generate_c_program(seed)
    assert first == second, (
        "Generator is not deterministic: two calls with seed=42 produced different programs."
    )


def test_determinism_across_multiple_seeds():
    """Check determinism for several seeds, not just one."""
    for seed in [0, 1, 99, 1000, 2**31 - 1]:
        a = generate_c_program(seed)
        b = generate_c_program(seed)
        assert a == b, f"Non-deterministic output for seed={seed}"


# ── 2. Seed isolation ─────────────────────────────────────────────────────────

def test_different_seeds_give_different_output():
    """Two different seeds should (almost certainly) produce different programs.
    If this ever fails legitimately it would be an extraordinary collision —
    treat it as a generator bug."""
    prog_0 = generate_c_program(0)
    prog_1 = generate_c_program(1)
    assert prog_0 != prog_1, (
        "Seeds 0 and 1 produced identical programs — the RNG is probably broken."
    )


# ── 3. Structure checks ───────────────────────────────────────────────────────

def test_output_is_string():
    """generate_c_program must return a str, not bytes or None."""
    result = generate_c_program(7)
    assert isinstance(result, str)


def test_contains_main():
    """Every generated program must have a main() function."""
    src = generate_c_program(42)
    assert "int main(void)" in src, "Generated program is missing 'int main(void)'."


def test_contains_include_stdio():
    """Programs use printf, so they must include stdio.h."""
    src = generate_c_program(42)
    assert "#include <stdio.h>" in src, "Generated program is missing '#include <stdio.h>'."


def test_contains_printf():
    """Every variable must be printed; there must be at least one printf call."""
    src = generate_c_program(42)
    assert "printf(" in src, "Generated program has no printf calls."


def test_uses_unsigned_int():
    """All variables should be unsigned int, not plain int."""
    src = generate_c_program(42)
    assert "unsigned int" in src, "Generated program does not use 'unsigned int'."


def test_ends_with_return_zero():
    """main() must return 0 so the exit code is defined."""
    src = generate_c_program(42)
    assert "return 0;" in src, "Generated program is missing 'return 0;'."


# ── 4. Compile without warnings ───────────────────────────────────────────────

# This test is skipped automatically if gcc is not on PATH.
# The @pytest.mark.skipif decorator evaluates the condition at collection time.
@pytest.mark.skipif(
    shutil.which("gcc") is None,
    reason="gcc not found on PATH — skipping compile test.",
)
def test_generated_program_compiles_without_warnings():
    """
    Write the generated program to a temp file, compile it with
    gcc -Wall -Wextra -std=c11, and assert that:
      - The compiler exits with code 0 (no errors).
      - stderr is empty (no warnings).

    This is the most important test: if it passes, the generator is not
    accidentally producing code with undefined behaviour that GCC detects.
    """
    src = generate_c_program(42)

    # Use a temporary directory so we don't litter the workspace.
    with tempfile.TemporaryDirectory() as tmp:
        src_path = Path(tmp) / "test_prog.c"
        bin_path = Path(tmp) / "test_prog"

        src_path.write_text(src)

        result = subprocess.run(
            ["gcc", "-Wall", "-Wextra", "-std=c11", str(src_path), "-o", str(bin_path)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, (
            f"Compilation failed.\nstderr:\n{result.stderr}"
        )
        assert result.stderr.strip() == "", (
            f"Compilation produced warnings.\nstderr:\n{result.stderr}"
        )
