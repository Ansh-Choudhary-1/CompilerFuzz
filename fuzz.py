"""
fuzz.py  —  Main CLI for CompilerFuzz
======================================
This is the entry point.  It ties generator.py and runner.py together into
a loop that:
  1. Generates a random C program (using the current seed).
  2. Compiles it with every compiler / optimisation-flag combination.
  3. Runs each binary and collects its output.
  4. Compares outputs.  If any two differ → mismatch → save artefacts.
  5. Moves to the next seed and repeats.
  6. Prints a summary when done.

Usage
-----
    python fuzz.py                         # 20 programs, seed 0, output in ./failures
    python fuzz.py --count 100 --seed 42   # 100 programs starting from seed 42
    python fuzz.py --output-dir /tmp/bugs  # custom failure folder

Run  python fuzz.py --help  to see all options.
"""

import argparse
import logging
import shutil
import sys
from pathlib import Path

from generator import generate_c_program
from runner   import compile_program, run_binary, all_combinations

# ── logging setup ─────────────────────────────────────────────────────────────
# The logging module is Python's standard way to print progress messages.
# Using logging (instead of bare print) lets us:
#   - Attach timestamps easily
#   - Change verbosity with a single flag (--verbose)
#   - Redirect to a file later without changing any other code

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── comparison logic ──────────────────────────────────────────────────────────

def compare_outputs(results: dict[str, str]) -> bool:
    """
    Check whether all compiler outputs are identical.

    Parameters
    ----------
    results : dict mapping label → stdout string.
              Example: {"gcc -O0": "a=3\\nb=7", "clang -O2": "a=3\\nb=9"}

    Returns
    -------
    True  if every value in the dict is the same string  (all agree).
    False if at least two values differ                  (mismatch found).

    This function is intentionally tiny so it is easy to test in isolation
    (see tests/test_comparator.py).
    """
    unique_outputs = set(results.values())   # a set collapses duplicates
    return len(unique_outputs) == 1          # exactly one unique value → all same


# ── failure saving ────────────────────────────────────────────────────────────

def save_failure(
    output_dir: Path,
    seed:       int,
    source:     str,
    results:    dict[str, str],
) -> Path:
    """
    Write artefacts for a mismatching program to disk.

    Creates:
        <output_dir>/<seed>/prog.c          — the C source
        <output_dir>/<seed>/<label>.txt     — one file per compiler/flag combo

    Parameters
    ----------
    output_dir : Root failures directory.
    seed       : The seed that produced this program (used as folder name).
    source     : The C source code string.
    results    : dict label → stdout (same dict passed to compare_outputs).

    Returns
    -------
    Path to the newly created seed folder.
    """
    seed_dir = output_dir / str(seed)
    seed_dir.mkdir(parents=True, exist_ok=True)

    # Save the source file.
    (seed_dir / "prog.c").write_text(source)

    # Save each compiler's output in its own text file.
    # Label is like "gcc_-O0"; replace spaces and hyphens for a safe filename.
    for label, stdout in results.items():
        safe_label = label.replace(" ", "_").replace("-", "")
        (seed_dir / f"{safe_label}.txt").write_text(stdout)

    return seed_dir


# ── per-program fuzzing logic ─────────────────────────────────────────────────

def fuzz_one(
    seed:       int,
    tmp_dir:    Path,
    output_dir: Path,
    combos:     list[tuple[str, str]],
) -> str:
    """
    Run one full fuzz iteration for a single seed.

    Returns one of: "pass", "mismatch", "compile_error", "timeout"

    Parameters
    ----------
    seed       : RNG seed for both generation and folder naming.
    tmp_dir    : Where to write the temporary .c file and binaries.
    output_dir : Where to save artefacts on mismatch.
    combos     : List of (compiler, opt_flag) pairs from runner.all_combinations().
    """
    # ── 1. Generate the C source ──────────────────────────────────────────────
    source = generate_c_program(seed)

    # Write to a temp file.  The filename encodes the seed so multiple seeds
    # can be in the same tmp_dir without colliding.
    src_path = tmp_dir / f"prog_{seed}.c"
    src_path.write_text(source)

    log.debug("Seed %d: source written to %s", seed, src_path)

    # ── 2. Compile + run each combination ────────────────────────────────────
    # outputs maps  "gcc -O0" → stdout_string  for the comparison step.
    outputs:        dict[str, str]  = {}
    had_compile_err = False
    had_timeout     = False

    for compiler, flag in combos:
        label = f"{compiler} {flag}"

        # Compile
        cr = compile_program(src_path, compiler, flag)

        if cr.skipped:
            # Compiler not installed — not an error in the program, just skip.
            continue

        if not cr.success:
            log.warning("Seed %d | %s: compile error — %s", seed, label, cr.stderr.strip())
            had_compile_err = True
            continue   # can't run if compilation failed

        # Run
        rr = run_binary(cr.binary_path)

        if rr.timed_out:
            log.warning("Seed %d | %s: run TIMED OUT", seed, label)
            had_timeout = True
            continue

        outputs[label] = rr.stdout
        log.debug("Seed %d | %s → %r", seed, label, rr.stdout)

    # ── 3. Compare ────────────────────────────────────────────────────────────
    # We need at least two outputs to make a meaningful comparison.
    if len(outputs) < 2:
        log.info("Seed %d: fewer than 2 outputs collected, skipping comparison.", seed)
        # Still report the error kinds we saw.
        if had_timeout:     return "timeout"
        if had_compile_err: return "compile_error"
        return "pass"   # only one compiler available; nothing to compare

    if compare_outputs(outputs):
        log.info("Seed %d: PASS  (%d combos agree)", seed, len(outputs))
        return "pass"
    else:
        # ── Mismatch found! ──────────────────────────────────────────────────
        # Log what each compiler produced so the difference is visible immediately.
        log.warning("Seed %d: MISMATCH!", seed)
        for label, out in outputs.items():
            log.warning("  %-20s → %r", label, out)

        save_dir = save_failure(output_dir, seed, source, outputs)
        log.warning("  Artefacts saved to: %s", save_dir)
        return "mismatch"


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_arg_parser() -> argparse.ArgumentParser:
    """Define and return the argument parser."""
    p = argparse.ArgumentParser(
        prog="fuzz.py",
        description=(
            "CompilerFuzz — differential testing for C compilers.\n"
            "Generates random C programs and checks that all compilers agree."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--count",
        type=int,
        default=20,
        help="Number of programs to test (default: 20).",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Starting seed; seeds used are seed, seed+1, ..., seed+count-1 (default: 0).",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=Path("failures"),
        help="Directory to save failing programs (default: ./failures).",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Show debug-level log messages (e.g. each program's output).",
    )
    return p


def main() -> None:
    args = build_arg_parser().parse_args()

    # Honour --verbose by lowering the log level to DEBUG.
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    combos = all_combinations()
    if not combos:
        log.error("No compiler/flag combinations defined in runner.py — nothing to do.")
        sys.exit(1)

    log.info("CompilerFuzz starting.")
    log.info("  Programs : %d  (seeds %d – %d)", args.count, args.seed, args.seed + args.count - 1)
    log.info("  Combos   : %s", [f"{c} {f}" for c, f in combos])
    log.info("  Failures : %s", args.output_dir.resolve())

    # Temporary directory for generated .c files and binaries.
    # We create it once and reuse it; it is cleaned up at the end.
    tmp_dir = Path("_compilerfuzz_tmp")
    tmp_dir.mkdir(exist_ok=True)

    # ── counters for the final summary ────────────────────────────────────────
    n_pass          = 0
    n_mismatch      = 0
    n_compile_error = 0
    n_timeout       = 0

    # ── main fuzzing loop ─────────────────────────────────────────────────────
    for i in range(args.count):
        seed   = args.seed + i
        status = fuzz_one(seed, tmp_dir, args.output_dir, combos)

        if   status == "pass":          n_pass          += 1
        elif status == "mismatch":      n_mismatch      += 1
        elif status == "compile_error": n_compile_error += 1
        elif status == "timeout":       n_timeout       += 1

    # ── clean up temp files ───────────────────────────────────────────────────
    shutil.rmtree(tmp_dir, ignore_errors=True)

    # ── final summary ─────────────────────────────────────────────────────────
    print()
    print("=" * 50)
    print("  CompilerFuzz Summary")
    print("=" * 50)
    print(f"  Total tested   : {args.count}")
    print(f"  Pass           : {n_pass}")
    print(f"  Mismatch ⚠️    : {n_mismatch}")
    print(f"  Compile error  : {n_compile_error}")
    print(f"  Timeout        : {n_timeout}")
    print("=" * 50)

    if n_mismatch > 0:
        print(f"\n  Failing programs saved in: {args.output_dir.resolve()}")

    # Exit with a non-zero code if mismatches were found — useful in CI pipelines.
    sys.exit(1 if n_mismatch > 0 else 0)


if __name__ == "__main__":
    main()
