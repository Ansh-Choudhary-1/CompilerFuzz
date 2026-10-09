"""
runner.py  —  Compile and run C programs for CompilerFuzz
==========================================================
This module has two jobs:
  1. Compile a .c source file with a given compiler and optimisation flag.
  2. Run the resulting binary and capture its stdout + exit code.

Everything is returned as plain dataclasses so the caller (fuzz.py) can
inspect results without parsing strings.

Public API
----------
    compile_program(source_path, compiler, opt_flag) -> CompileResult
    run_binary(binary_path, timeout)               -> RunResult

Configuration
-------------
Edit COMPILERS and OPT_FLAGS below to add or remove compilers / flags.
That's the only place you need to touch.
"""

import subprocess
import shutil
import dataclasses
from pathlib import Path

# ── configuration — edit here to add compilers or flags ──────────────────────

# Each entry is the executable name as it appears on PATH.
COMPILERS: list[str] = ["gcc", "clang"]

# Each program is compiled once per flag.
# -O0 = no optimisation, -O2 = standard release optimisation.
# Differences between O0 and O2 output are the most common source of
# compiler bugs (the optimiser transforms code and sometimes changes meaning).
OPT_FLAGS: list[str] = ["-O0", "-O2"]

# How long (seconds) we allow a compiled binary to run before killing it.
DEFAULT_TIMEOUT: int = 5

# ── result types ──────────────────────────────────────────────────────────────

@dataclasses.dataclass
class CompileResult:
    """
    Outcome of one compilation attempt.

    Attributes
    ----------
    success     : True if the compiler exited with code 0.
    binary_path : Path to the compiled binary (only valid when success=True).
    compiler    : The compiler executable used, e.g. "gcc".
    opt_flag    : The optimisation flag used, e.g. "-O2".
    stderr      : Raw compiler error / warning text (useful for debugging).
    skipped     : True if the compiler was not found on PATH (not an error).
    """
    success:     bool
    binary_path: Path | None
    compiler:    str
    opt_flag:    str
    stderr:      str
    skipped:     bool = False


@dataclasses.dataclass
class RunResult:
    """
    Outcome of running one compiled binary.

    Attributes
    ----------
    stdout    : Everything the binary printed to stdout (stripped).
    exit_code : Process exit code (0 = normal).
    timed_out : True if the binary was killed because it exceeded the timeout.
    """
    stdout:    str
    exit_code: int
    timed_out: bool


# ── compile ───────────────────────────────────────────────────────────────────

def compile_program(
    source_path: Path,
    compiler:    str,
    opt_flag:    str,
) -> CompileResult:
    """
    Compile `source_path` with `compiler` and `opt_flag`.

    The output binary is placed next to the source file with a name that
    encodes the compiler and flag so multiple compilations don't overwrite
    each other.  Example:
        /tmp/prog_42.c  →  /tmp/prog_42_gcc_O2

    Parameters
    ----------
    source_path : Path to the .c file.
    compiler    : Compiler executable name, e.g. "gcc" or "clang".
    opt_flag    : Optimisation flag, e.g. "-O0" or "-O2".

    Returns
    -------
    CompileResult
    """
    # Check whether the compiler is even installed before trying.
    # shutil.which() searches PATH exactly like the shell does.
    if shutil.which(compiler) is None:
        print(f"  [SKIP] {compiler} not found on PATH — skipping.")
        return CompileResult(
            success=False,
            binary_path=None,
            compiler=compiler,
            opt_flag=opt_flag,
            stderr="",
            skipped=True,
        )

    # Build the output binary path.
    # Strip the leading "-" from the flag for a clean filename, e.g. "O2".
    flag_tag    = opt_flag.lstrip("-")          # "-O2" → "O2"
    binary_name = f"{source_path.stem}_{compiler}_{flag_tag}"
    binary_path = source_path.parent / binary_name

    # Assemble the compiler command.
    # -Wall turns on most warnings; we want to know if the generator
    # accidentally produces code with warnings (it shouldn't).
    cmd = [
        compiler,
        opt_flag,
        "-Wall",            # enable all common warnings
        "-Wextra",          # enable extra warnings
        "-std=c11",         # compile as C11
        str(source_path),   # input file
        "-o", str(binary_path),  # output binary
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,   # capture both stdout and stderr
            text=True,             # decode bytes → str automatically
            timeout=30,            # compiler itself shouldn't hang, but guard it
        )
    except subprocess.TimeoutExpired:
        return CompileResult(
            success=False,
            binary_path=None,
            compiler=compiler,
            opt_flag=opt_flag,
            stderr="Compiler timed out.",
        )

    return CompileResult(
        success=result.returncode == 0,
        binary_path=binary_path if result.returncode == 0 else None,
        compiler=compiler,
        opt_flag=opt_flag,
        stderr=result.stderr,
    )


# ── run ───────────────────────────────────────────────────────────────────────

def run_binary(
    binary_path: Path,
    timeout: int = DEFAULT_TIMEOUT,
) -> RunResult:
    """
    Execute `binary_path` and capture its stdout.

    Parameters
    ----------
    binary_path : Path to the compiled binary.
    timeout     : Seconds before the process is killed (default 5).

    Returns
    -------
    RunResult
    """
    try:
        result = subprocess.run(
            [str(binary_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return RunResult(
            stdout=result.stdout.strip(),   # strip trailing newline for clean comparison
            exit_code=result.returncode,
            timed_out=False,
        )

    except subprocess.TimeoutExpired:
        return RunResult(
            stdout="",
            exit_code=-1,   # sentinel: -1 means we killed it
            timed_out=True,
        )

    except PermissionError:
        # Can happen on some systems if the binary isn't executable.
        return RunResult(
            stdout="",
            exit_code=-2,   # sentinel: -2 means permission problem
            timed_out=False,
        )


# ── convenience: all compiler+flag combinations ───────────────────────────────

def all_combinations() -> list[tuple[str, str]]:
    """
    Return every (compiler, opt_flag) pair from the configuration above.

    Example output: [("gcc", "-O0"), ("gcc", "-O2"), ("clang", "-O0"), ("clang", "-O2")]

    fuzz.py calls this so it doesn't need to import COMPILERS / OPT_FLAGS directly.
    """
    return [(compiler, flag) for compiler in COMPILERS for flag in OPT_FLAGS]


# ── quick manual test ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    """
    Run this file directly to test a single compilation + execution.
    Usage:  python runner.py <path_to_c_file>
    Example: python runner.py /tmp/test.c
    """
    import sys

    if len(sys.argv) < 2:
        print("Usage: python runner.py <path_to_c_file>")
        sys.exit(1)

    src = Path(sys.argv[1])

    for compiler, flag in all_combinations():
        print(f"\n── {compiler} {flag} ──")

        cr = compile_program(src, compiler, flag)
        if cr.skipped:
            print("  Skipped (compiler not found).")
            continue
        if not cr.success:
            print(f"  Compile FAILED:\n{cr.stderr}")
            continue

        print(f"  Compiled → {cr.binary_path}")

        rr = run_binary(cr.binary_path)
        if rr.timed_out:
            print("  Run TIMED OUT.")
        else:
            print(f"  Exit code : {rr.exit_code}")
            print(f"  Output    :\n{rr.stdout}")
