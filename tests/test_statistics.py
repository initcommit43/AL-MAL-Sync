"""Tests for sync/statistics.py: per-media-type counts derived from a
SyncOutcome, aggregate totals across media types, and table formatting."""

from __future__ import annotations

from al_mal_sync.models import Anime
from al_mal_sync.sync.statistics import MediaTypeStats, SyncStatistics, format_statistics_table
from al_mal_sync.sync.updater import SyncOutcome


def _outcome(*, updated=0, skipped=0, dry_run=0, errors=0, unmatched=0) -> SyncOutcome:
    outcome = SyncOutcome()
    outcome.updated = [Anime() for _ in range(updated)]
    outcome.skipped = [Anime() for _ in range(skipped)]
    outcome.dry_run = [Anime() for _ in range(dry_run)]
    outcome.errors = [(Anime(), "boom") for _ in range(errors)]
    outcome.unmatched = [object() for _ in range(unmatched)]  # type: ignore[list-item]
    return outcome


class TestMediaTypeStats:
    def test_from_outcome_counts_each_bucket(self) -> None:
        stats = MediaTypeStats.from_outcome(
            "anime", _outcome(updated=3, skipped=2, dry_run=1, errors=1, unmatched=4)
        )
        assert stats == MediaTypeStats(
            media_type="anime", total=11, updated=3, skipped=2, dry_run=1, errors=1, unmatched=4
        )

    def test_empty_outcome_is_all_zero(self) -> None:
        stats = MediaTypeStats.from_outcome("manga", SyncOutcome())
        assert stats == MediaTypeStats(media_type="manga")


class TestSyncStatistics:
    def test_from_outcomes_builds_one_row_per_kind(self) -> None:
        stats = SyncStatistics.from_outcomes(
            {"anime": _outcome(updated=1), "manga": _outcome(skipped=2)}
        )
        assert [s.media_type for s in stats.per_media_type] == ["anime", "manga"]

    def test_total_sums_across_media_types(self) -> None:
        stats = SyncStatistics.from_outcomes(
            {"anime": _outcome(updated=3, errors=1), "manga": _outcome(updated=2, skipped=1)}
        )
        total = stats.total
        assert total.media_type == "total"
        assert total.updated == 5
        assert total.skipped == 1
        assert total.errors == 1
        assert total.total == 7

    def test_total_of_empty_stats_is_zero(self) -> None:
        assert SyncStatistics([]).total == MediaTypeStats(media_type="total")


class TestFormatStatisticsTable:
    def test_single_media_type_has_no_total_row(self) -> None:
        table = format_statistics_table(SyncStatistics.from_outcomes({"anime": _outcome(updated=1)}))
        lines = table.splitlines()
        assert len(lines) == 2  # header + one data row
        assert "total" not in lines[1]

    def test_multiple_media_types_appends_total_row(self) -> None:
        table = format_statistics_table(
            SyncStatistics.from_outcomes({"anime": _outcome(updated=1), "manga": _outcome(updated=2)})
        )
        lines = table.splitlines()
        assert len(lines) == 4  # header + anime + manga + total
        assert lines[-1].startswith("total")

    def test_columns_are_aligned(self) -> None:
        table = format_statistics_table(
            SyncStatistics.from_outcomes({"anime": _outcome(updated=100), "manga": _outcome(updated=1)})
        )
        lines = table.splitlines()
        # Both rows' second column ("total" count) must start at the same offset.
        header_col_start = lines[0].index("total")
        assert lines[1][header_col_start:].split()[0] in ("100",)
