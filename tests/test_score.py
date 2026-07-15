"""Tests for AniList<->MAL score normalization."""

from __future__ import annotations

import pytest

from al_mal_sync.sync.score import (
    POINT_3,
    POINT_5,
    POINT_10,
    POINT_10_DECIMAL,
    POINT_100,
    denormalize_score_for_anilist,
    normalize_score_for_mal,
)


class TestNormalizeScoreForMal:
    def test_zero_is_always_zero(self) -> None:
        assert normalize_score_for_mal(0, POINT_100) == 0

    def test_point_100_rounds_half_up(self) -> None:
        # 85 / 10 = 8.5; half-up rounds to 9 (banker's rounding would give 8).
        assert normalize_score_for_mal(85, POINT_100) == 9

    @pytest.mark.parametrize("fmt", [POINT_10, POINT_10_DECIMAL])
    def test_point_10_variants_pass_through(self, fmt: str) -> None:
        assert normalize_score_for_mal(7, fmt) == 7

    def test_point_5_doubles(self) -> None:
        assert normalize_score_for_mal(4, POINT_5) == 8

    def test_point_3_scales_up(self) -> None:
        # 2 / 3 * 10 = 6.67 -> rounds to 7.
        assert normalize_score_for_mal(2, POINT_3) == 7

    def test_unknown_format_over_10_treated_as_point_100(self) -> None:
        assert normalize_score_for_mal(70, "SOMETHING_ELSE") == 7

    def test_unknown_format_under_10_passes_through(self) -> None:
        assert normalize_score_for_mal(7, "SOMETHING_ELSE") == 7

    def test_clamps_above_10(self) -> None:
        assert normalize_score_for_mal(15, POINT_10) == 10


class TestDenormalizeScoreForAnilist:
    def test_zero_is_always_zero(self) -> None:
        assert denormalize_score_for_anilist(0, POINT_100) == 0

    def test_point_100_scales_up(self) -> None:
        assert denormalize_score_for_anilist(9, POINT_100) == 90

    @pytest.mark.parametrize("fmt", [POINT_10, POINT_10_DECIMAL])
    def test_point_10_variants_pass_through(self, fmt: str) -> None:
        assert denormalize_score_for_anilist(7, fmt) == 7

    def test_point_5_halves(self) -> None:
        assert denormalize_score_for_anilist(8, POINT_5) == 4

    def test_point_3_scales_down(self) -> None:
        # 7 / 10 * 3 = 2.1 -> rounds to 2.
        assert denormalize_score_for_anilist(7, POINT_3) == 2

    def test_point_3_never_rounds_down_to_zero(self) -> None:
        # 1 / 10 * 3 = 0.3, floored at 1 instead of rounding to 0 (a MAL score of
        # 1 should never silently disappear on AniList).
        assert denormalize_score_for_anilist(1, POINT_3) == 1
