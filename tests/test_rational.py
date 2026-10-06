"""Tests for exact rational parsing, matrix validation and propagation."""

from fractions import Fraction

import pytest

from app.rational import (
    format_fraction,
    is_positive_semidefinite,
    is_symmetric,
    parse_rational,
    propagate,
)


class TestParseRational:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (0, Fraction(0)),
            (5, Fraction(5)),
            (-7, Fraction(-7)),
            ("0", Fraction(0)),
            ("1/2", Fraction(1, 2)),
            ("-3/4", Fraction(-3, 4)),
            (12345678901234567890, Fraction(12345678901234567890, 1)),
            ("10000000000000000001/3", Fraction(10000000000000000001, 3)),
        ],
    )
    def test_accepted(self, raw, expected):
        assert parse_rational(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "2/4",          # not reduced
            "5/1",          # integer must be bare
            "-6/2",         # not reduced
            "1/0",          # zero denominator
            "1/-2",         # negative denominator
            "1/2/3",        # malformed
            "",             # empty
            "abc",
            0.5,            # floats are inexact
            -1.0,
            True,           # booleans are not measurements
            False,
            None,
            ["1/2"],
            {"num": 1},
        ],
    )
    def test_rejected(self, raw):
        with pytest.raises(ValueError):
            parse_rational(raw)


class TestSymmetry:
    def test_exact_symmetry(self):
        m = [[Fraction(1), Fraction(1, 2)], [Fraction(1, 2), Fraction(3)]]
        assert is_symmetric(m, 2)

    def test_asymmetric_rationals(self):
        m = [[Fraction(1), Fraction(1, 2)], [Fraction(1, 3), Fraction(3)]]
        assert not is_symmetric(m, 2)


class TestPSD:
    @pytest.mark.parametrize(
        "rows",
        [
            [[1]],
            [[0]],
            [[2, 1], [1, 2]],
            [[0, 0], [0, 1]],                 # zero leading row, needs pivoting
            [[4, 2], [2, 3]],
            [[1, 1], [1, 1]],                 # singular
            [[0, 0, 0], [0, 1, 1], [0, 1, 1]],
            [[1, Fraction(1, 2), 0], [Fraction(1, 2), 1, 0], [0, 0, 0]],
        ],
    )
    def test_psd_accepted(self, rows):
        matrix = [[Fraction(x) for x in row] for row in rows]
        assert is_positive_semidefinite(matrix, len(matrix))

    @pytest.mark.parametrize(
        "rows",
        [
            [[-1]],
            [[0, 1], [1, 0]],                 # zero diagonal, indefinite
            [[1, 2], [2, 1]],
            [[0, 0], [0, -1]],
            [[2, 3], [3, 2]],
            [[1, 1, 0], [1, 1, 1], [0, 1, 1]],
        ],
    )
    def test_non_psd_rejected(self, rows):
        matrix = [[Fraction(x) for x in row] for row in rows]
        assert not is_positive_semidefinite(matrix, len(matrix))

    def test_full_size_matrix(self):
        n = 24
        # PSD matrix: C = I + (1/n) * 11^T, exact rationals
        matrix = [
            [
                Fraction(1) + (Fraction(1, n) if i != j else Fraction(0))
                for j in range(n)
            ]
            for i in range(n)
        ]
        for i in range(n):
            matrix[i][i] = Fraction(1) + Fraction(1, n)
        assert is_positive_semidefinite(matrix, n)


class TestPropagate:
    def test_single_input(self):
        estimate, variance = propagate(
            values=[Fraction(1, 2)],
            sensitivities=[Fraction(2)],
            covariance=[[Fraction(3)]],
            intercept=Fraction(1),
        )
        assert estimate == Fraction(2)
        assert variance == Fraction(12)

    def test_correlated_inputs_exact(self):
        estimate, variance = propagate(
            values=[Fraction(1), Fraction(2)],
            sensitivities=[Fraction(1, 2), Fraction(1, 3)],
            covariance=[[Fraction(4), Fraction(1)], [Fraction(1), Fraction(9)]],
            intercept=Fraction(1),
        )
        # 1 + 1/2*1 + 1/3*2 = 13/6
        assert estimate == Fraction(13, 6)
        # (1/2)^2*4 + 2*(1/2)*(1/3)*1 + (1/3)^2*9 = 1 + 1/3 + 1 = 7/3
        assert variance == Fraction(7, 3)

    def test_zero_sensitivity_gives_zero_variance(self):
        _, variance = propagate(
            values=[Fraction(5)],
            sensitivities=[Fraction(0)],
            covariance=[[Fraction(99)]],
            intercept=Fraction(0),
        )
        assert variance == Fraction(0)


class TestFormat:
    @pytest.mark.parametrize(
        ("value", "text"),
        [
            (Fraction(0), "0"),
            (Fraction(7), "7"),
            (Fraction(7, 3), "7/3"),
            (Fraction(-1, 2), "-1/2"),
        ],
    )
    def test_format(self, value, text):
        assert format_fraction(value) == text
