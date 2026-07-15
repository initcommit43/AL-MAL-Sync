"""Tests for start/finish date sync edge cases."""

from __future__ import annotations

from datetime import date

from al_mal_sync.sync.dates import parse_mal_date, same_dates


class TestSameDates:
    def test_both_none_is_same(self) -> None:
        assert same_dates(None, None) is True

    def test_source_none_target_set_is_same(self) -> None:
        # A missing source date must never clear an existing target date.
        assert same_dates(None, date(2020, 1, 1)) is True

    def test_source_set_target_none_is_different(self) -> None:
        assert same_dates(date(2020, 1, 1), None) is False

    def test_equal_dates_are_same(self) -> None:
        assert same_dates(date(2020, 1, 1), date(2020, 1, 1)) is True

    def test_different_dates_are_different(self) -> None:
        assert same_dates(date(2020, 1, 1), date(2020, 1, 2)) is False


class TestParseMalDate:
    def test_empty_string_returns_none(self) -> None:
        assert parse_mal_date("") is None

    def test_valid_date_parses(self) -> None:
        assert parse_mal_date("2020-06-15") == date(2020, 6, 15)

    def test_partial_date_returns_none(self) -> None:
        assert parse_mal_date("2020") is None

    def test_garbage_returns_none(self) -> None:
        assert parse_mal_date("not-a-date") is None
