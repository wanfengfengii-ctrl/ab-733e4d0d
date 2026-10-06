"""Exact rational arithmetic core for uncertainty propagation.

Every measurement, sensitivity, covariance entry and intercept is held as a
:class:`fractions.Fraction`, so the decision (within budget / over budget) is
never altered by floating-point approximation.
"""

from __future__ import annotations

from fractions import Fraction
from typing import List, Sequence, Tuple

MIN_INPUTS = 1
MAX_INPUTS = 24


def parse_rational(value: object) -> Fraction:
    """Parse an integer or a reduced ``p/q`` string with positive denominator.

    Accepted forms:

    * JSON integer or string of digits (optionally signed)
    * string ``"p/q"`` where ``p`` and ``q`` are integers, ``q > 0`` and the
      fraction is in lowest terms (gcd(|p|, q) == 1)

    Floating-point JSON numbers are rejected on purpose: they are an inexact
    representation and must not enter an exact budget.
    """
    if isinstance(value, bool):  # bool is an int subclass; never treat as 0/1
        raise ValueError("must be an integer or a reduced rational string p/q")
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("empty rational string")
        parts = text.split("/")
        if len(parts) == 1:
            num_text = parts[0]
            den_text = "1"
        elif len(parts) == 2:
            num_text, den_text = parts
            num_text = num_text.strip()
            den_text = den_text.strip()
        else:
            raise ValueError("rational must be an integer or p/q")
        try:
            numerator = int(num_text)
            denominator = int(den_text)
        except ValueError:
            raise ValueError("rational components must be integers") from None
        if denominator <= 0:
            raise ValueError("rational denominator must be positive")
        if denominator == 1 and len(parts) == 2:
            # "2/1" is not reduced; require canonical form
            raise ValueError("rational string must be in lowest terms")
        fraction = Fraction(numerator, denominator)
        if len(parts) == 2 and fraction.denominator != denominator:
            raise ValueError("rational string must be in lowest terms")
        return fraction
    raise ValueError("must be an integer or a reduced rational string p/q")


def is_symmetric(matrix: Sequence[Sequence[Fraction]], n: int) -> bool:
    """Exact symmetry check: every off-diagonal pair equal as Fractions."""
    for i in range(n):
        for j in range(i + 1, n):
            if matrix[i][j] != matrix[j][i]:
                return False
    return True


def is_positive_semidefinite(matrix: Sequence[Sequence[Fraction]], n: int) -> bool:
    """Exact symmetric positive-semidefinite test via LDL^T with symmetric
    (largest-pivot) pivoting.

    All arithmetic is rational. The matrix is PSD exactly when every pivot
    selected by the pivoting rule is non-negative.

    A plain unpivoted LDLᵀ would falsely reject some definite matrices whose
    leading minor is zero (e.g. ``[[0,0],[0,1]]``); symmetric pivoting handles
    those: a zero row/column is inert and can be skipped, while an indefinite
    matrix like ``[[0,1],[1,0]]`` exposes a negative Schur complement.
    """
    # Work on a mutable copy of Fraction references (Fractions are immutable).
    a: List[List[Fraction]] = [list(row) for row in matrix]
    remaining = list(range(n))

    while remaining:
        # Choose the remaining index with largest absolute diagonal entry.
        pivot_local = max(
            range(len(remaining)),
            key=lambda k: abs(a[remaining[k]][remaining[k]]),
        )
        pivot_pos = pivot_local
        pivot_idx = remaining[pivot_pos]
        pivot = a[pivot_idx][pivot_idx]

        if pivot == 0:
            # Largest remaining diagonal is zero. The block is PSD only if the
            # whole trailing block is zero; any nonzero entry there implies a
            # negative direction (2x2 [[0,b],[b,*]] is indefinite for b != 0).
            for i in remaining:
                for j in remaining:
                    if a[i][j] != 0:
                        return False
            return True

        if pivot < 0:
            return False

        others = [idx for pos, idx in enumerate(remaining) if pos != pivot_pos]
        # Schur complement. Each ordered pair is visited exactly once via its
        # outer row; the pivot row/column are never members of `others`, so the
        # factors read below are not mutated while the loop runs.
        for i in others:
            multiplier = a[i][pivot_idx] / pivot  # l_{i,k}
            for j in others:
                a[i][j] -= multiplier * a[pivot_idx][j]
        remaining = others

    return True


def propagate(
    values: Sequence[Fraction],
    sensitivities: Sequence[Fraction],
    covariance: Sequence[Sequence[Fraction]],
    intercept: Fraction,
) -> Tuple[Fraction, Fraction]:
    """Return ``(estimate, combined_variance)`` exactly.

    estimate   = intercept + sum_i sensitivity_i * value_i
    variance   = sum_i sum_j sensitivity_i * C_ij * sensitivity_j
    """
    n = len(values)
    estimate = intercept
    for i in range(n):
        estimate += sensitivities[i] * values[i]

    variance = Fraction(0)
    for i in range(n):
        row_sum = Fraction(0)
        for j in range(n):
            row_sum += covariance[i][j] * sensitivities[j]
        variance += sensitivities[i] * row_sum
    return estimate, variance


def format_fraction(value: Fraction) -> str:
    """Canonical wire format: bare integer if denominator is 1, else p/q."""
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"
