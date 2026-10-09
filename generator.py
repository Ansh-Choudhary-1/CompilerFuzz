"""
generator.py  —  Random C program generator for CompilerFuzz
=============================================================
Public API
----------
    generate_c_program(seed: int) -> str
        Returns a complete, valid C program as a string.
        The same seed always produces the same program (deterministic).

Design rules (all exist to avoid undefined behaviour in C)
----------------------------------------------------------
  - Only `unsigned int` arithmetic  →  overflow wraps predictably (C11 §6.2.5)
  - Every variable is initialised before use
  - No division or modulo            →  no division-by-zero risk
  - No variable-amount shifts        →  shift-by-≥-width is UB in C
  - Loop bounds are compile-time constants  →  no infinite loops
  - No pointers, no arrays, no heap  →  no out-of-bounds risk
  - All comparisons produce 0 or 1 (unsigned), stored back as unsigned

These constraints mean the generated program is well-defined C and
two compilers MUST agree on its output.  Any disagreement is a
candidate compiler bug.
"""

import random
import textwrap

# ── tuneable constants ────────────────────────────────────────────────────────

MIN_VARS   = 3      # fewest variables in a program
MAX_VARS   = 6      # most variables
MIN_STMTS  = 4      # fewest random statements after initialisation
MAX_STMTS  = 10     # most random statements
LOOP_ITERS = 4      # fixed for-loop iteration count (never infinite)

# Arithmetic/bitwise operators that are safe with unsigned ints
# (no division, no variable shifts)
BINARY_OPS = ["+", "-", "*", "&", "|", "^"]

# Comparison operators — result is 0 or 1, cast back to unsigned int
COMPARE_OPS = ["==", "!=", "<", ">", "<=", ">="]

# ── helpers ───────────────────────────────────────────────────────────────────

def _make_var_names(rng: random.Random, count: int) -> list[str]:
    """Return a list of simple variable names like ['a', 'b', 'c', ...]."""
    # Use letters a-z; count is small (3-6) so we won't run out.
    letters = "abcdefghijklmnopqrstuvwxyz"
    return list(letters[:count])


def _random_expr(rng: random.Random, vars_: list[str], depth: int = 0) -> str:
    """
    Build a random arithmetic expression using existing variables or small
    unsigned integer literals.

    `depth` limits recursion so we don't produce absurdly deep expressions.
    At depth 0 we may recurse once; at depth >= 1 we only emit leaves.
    """
    # At maximum depth (or randomly), just return a leaf.
    if depth >= 1 or rng.random() < 0.4:
        if rng.random() < 0.6:
            # Use an existing variable
            return rng.choice(vars_)
        else:
            # Use a small literal — keep it non-zero occasionally for variety
            return str(rng.randint(1, 15))

    # Otherwise build a binary expression.
    left  = _random_expr(rng, vars_, depth + 1)
    right = _random_expr(rng, vars_, depth + 1)
    op    = rng.choice(BINARY_OPS)
    return f"({left} {op} {right})"


def _random_statement(rng: random.Random, vars_: list[str]) -> str:
    """
    Return one random C statement (as a string, no trailing newline).
    All statements are assignments so every variable stays meaningful.

    Possible statement shapes:
      1. Simple assignment      :  a = (b + c);
      2. Compound assignment    :  a += b;
      3. if/else assignment     :  if (a < b) { a = expr; } else { a = expr; }
      4. Comparison assignment  :  a = (b < c);   // yields 0 or 1
    """
    target = rng.choice(vars_)          # variable being written to
    kind   = rng.randint(1, 4)

    if kind == 1:
        # Simple assignment with a random expression
        expr = _random_expr(rng, vars_)
        return f"    {target} = {expr};"

    elif kind == 2:
        # Compound assignment — safe operators only (no /=, no shift=)
        safe_compound = ["+=", "-=", "*=", "&=", "|=", "^="]
        op   = rng.choice(safe_compound)
        leaf = _random_expr(rng, vars_, depth=1)  # keep right-hand side simple
        return f"    {target} {op} {leaf};"

    elif kind == 3:
        # if / else — both branches assign to the same variable
        cond_left  = rng.choice(vars_)
        cond_right = _random_expr(rng, vars_, depth=1)
        cmp_op     = rng.choice(COMPARE_OPS)
        then_expr  = _random_expr(rng, vars_)
        else_expr  = _random_expr(rng, vars_)
        # Format as a compact but readable single-line if/else block
        return (
            f"    if ({cond_left} {cmp_op} {cond_right}) "
            f"{{ {target} = {then_expr}; }} "
            f"else {{ {target} = {else_expr}; }}"
        )

    else:
        # Comparison result stored as 0u or 1u
        cond_left  = rng.choice(vars_)
        cond_right = _random_expr(rng, vars_, depth=1)
        cmp_op     = rng.choice(COMPARE_OPS)
        return f"    {target} = ({cond_left} {cmp_op} {cond_right});"


def _for_loop_block(rng: random.Random, vars_: list[str]) -> list[str]:
    """
    Generate a bounded for-loop with a fixed iteration count (LOOP_ITERS).
    The loop variable 'i' is local and never conflicts with program variables
    because we use letters a-z for program vars and 'i' is declared inside.
    Inside the loop we do one random statement per iteration.

    Returns a list of lines (strings).
    """
    lines = []
    lines.append(f"    for (unsigned int i = 0; i < {LOOP_ITERS}u; i++) {{")
    # One random body statement; indent it an extra level
    body = _random_statement(rng, vars_)
    lines.append("    " + body.strip())   # add one extra indent (4 spaces)
    lines.append("    }")
    return lines


# ── public API ────────────────────────────────────────────────────────────────

def generate_c_program(seed: int) -> str:
    """
    Generate a complete, valid C program and return it as a string.

    Parameters
    ----------
    seed : int
        RNG seed.  The same seed always produces the same program.

    Returns
    -------
    str
        A C program that can be written to a .c file and compiled.
    """
    # Create a LOCAL Random instance so this function never touches the
    # global random state, and concurrent calls with different seeds are safe.
    rng = random.Random(seed)

    # 1. Decide how many variables this program will have.
    num_vars = rng.randint(MIN_VARS, MAX_VARS)
    vars_    = _make_var_names(rng, num_vars)

    # 2. Decide initial values (small positive numbers so * doesn't overflow
    #    immediately and wipe out all interesting variation).
    init_vals = [rng.randint(1, 20) for _ in vars_]

    # ── start building the source lines ──────────────────────────────────────

    lines = []

    # Standard header comment so the seed is visible inside the file.
    lines.append(f"/* CompilerFuzz generated program  seed={seed} */")
    lines.append("#include <stdio.h>")
    lines.append("")
    lines.append("int main(void) {")

    # 3. Declare and initialise all variables.
    for name, val in zip(vars_, init_vals):
        # `unsigned int` — wraps on overflow, never UB
        lines.append(f"    unsigned int {name} = {val}u;")

    lines.append("")  # blank line for readability

    # 4. Generate random statements.
    num_stmts = rng.randint(MIN_STMTS, MAX_STMTS)
    for _ in range(num_stmts):
        # 20 % chance of emitting a for-loop instead of a plain statement
        if rng.random() < 0.2:
            lines.extend(_for_loop_block(rng, vars_))
        else:
            lines.append(_random_statement(rng, vars_))

    lines.append("")  # blank line before print block

    # 5. Print every variable so the compiler can't optimise them away.
    #    %u is the correct printf format specifier for unsigned int.
    for name in vars_:
        lines.append(f'    printf("{name}=%u\\n", {name});')

    lines.append("    return 0;")
    lines.append("}")

    return "\n".join(lines) + "\n"


# ── quick manual test ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Running `python generator.py` prints a sample program to stdout.
    import sys
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 42
    print(generate_c_program(seed))
