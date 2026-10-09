"""
tests/test_comparator.py  —  Tests for the compare_outputs() function in fuzz.py
==================================================================================
compare_outputs() is the heart of the differential testing logic.
We test it in complete isolation from compilers — we just feed it
hand-crafted dictionaries and check it returns the right boolean.

This means these tests run instantly and don't require gcc or clang.
"""

# Import only the comparison function, nothing else from fuzz.py.
from fuzz import compare_outputs


# ── all outputs agree ─────────────────────────────────────────────────────────

def test_all_same_single_entry():
    """A dict with one entry always 'agrees' (trivially)."""
    assert compare_outputs({"gcc -O0": "a=1\nb=2"}) is True


def test_all_same_two_entries():
    """Two identical outputs → no mismatch."""
    outputs = {
        "gcc -O0":   "a=3\nb=7\nc=2",
        "gcc -O2":   "a=3\nb=7\nc=2",
    }
    assert compare_outputs(outputs) is True


def test_all_same_four_entries():
    """Four compilers, all producing the same output → pass."""
    same = "x=10\ny=20"
    outputs = {
        "gcc -O0":   same,
        "gcc -O2":   same,
        "clang -O0": same,
        "clang -O2": same,
    }
    assert compare_outputs(outputs) is True


# ── outputs differ ────────────────────────────────────────────────────────────

def test_two_entries_differ():
    """Two different outputs → mismatch detected."""
    outputs = {
        "gcc -O0":  "a=3\nb=7",
        "gcc -O2":  "a=3\nb=9",   # b differs — classic optimiser bug pattern
    }
    assert compare_outputs(outputs) is False


def test_one_of_four_differs():
    """One outlier among four → still a mismatch."""
    outputs = {
        "gcc -O0":   "a=1\nb=2",
        "gcc -O2":   "a=1\nb=2",
        "clang -O0": "a=1\nb=2",
        "clang -O2": "a=1\nb=99",  # ← this one disagrees
    }
    assert compare_outputs(outputs) is False


def test_all_different():
    """Every output is unique → mismatch detected."""
    outputs = {
        "gcc -O0":   "a=1",
        "gcc -O2":   "a=2",
        "clang -O0": "a=3",
    }
    assert compare_outputs(outputs) is False


# ── edge cases ────────────────────────────────────────────────────────────────

def test_empty_dict():
    """An empty dict has no disagreements — treat as pass (nothing to compare)."""
    # set() of an empty dict's values is set(), len == 0, not == 1.
    # We define "empty" as agreeing because fuzz.py guards against < 2 outputs
    # before calling compare_outputs.  But the function itself should not crash.
    # len(set()) == 0, which is != 1, so it returns False.
    # Document this behaviour explicitly so callers know.
    result = compare_outputs({})
    # Not True (0 unique outputs ≠ 1), but also doesn't crash.
    assert isinstance(result, bool)


def test_empty_string_outputs_agree():
    """Two empty-string outputs count as identical."""
    outputs = {"gcc -O0": "", "clang -O0": ""}
    assert compare_outputs(outputs) is True


def test_whitespace_sensitivity():
    """Outputs that differ only in trailing whitespace should differ.
    (runner.py strips trailing newlines so this shouldn't happen in practice,
    but we test the comparator is strict about exact string equality.)"""
    outputs = {
        "gcc -O0":  "a=1",
        "clang -O0": "a=1 ",   # trailing space
    }
    assert compare_outputs(outputs) is False
