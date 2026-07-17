"""Tests for sync/report.py: aggregating warnings/conflicts/unmapped counts
from one run's SyncOutcomes (plus optional favorites results) into a single
SyncReport, and formatting it as text."""

from __future__ import annotations

from al_mal_sync.mapping.strategies import MatchResult
from al_mal_sync.models import Anime
from al_mal_sync.sync.conflict import Conflict
from al_mal_sync.sync.favorites import FavoritesOutcome
from al_mal_sync.sync.report import SyncReport, build_report, format_report
from al_mal_sync.sync.updater import SyncOutcome


def _anime(title: str = "Show") -> Anime:
    return Anime(title_en=title)


def _conflict() -> Conflict:
    winner = _anime("Winner")
    loser = _anime("Loser")
    match = MatchResult(target=_anime("Target"), strategy_name="TitleStrategy", strategy_idx=5)
    return Conflict(source=loser, target_id=42, winner_source=winner, strategy_name=match.strategy_name)


class TestBuildReport:
    def test_empty_outcomes_produce_empty_report(self) -> None:
        report = build_report({"anime": SyncOutcome()})
        assert report.is_empty()

    def test_collects_warnings_conflicts_and_unmapped_count_across_kinds(self) -> None:
        anime_outcome = SyncOutcome()
        anime_outcome.warnings = ["episode count mismatch"]
        anime_outcome.conflicts = [_conflict()]
        anime_outcome.unmatched = [object(), object()]  # type: ignore[list-item]

        manga_outcome = SyncOutcome()
        manga_outcome.unmatched = [object()]  # type: ignore[list-item]

        report = build_report({"anime": anime_outcome, "manga": manga_outcome})

        assert report.warnings == ["episode count mismatch"]
        assert len(report.conflicts) == 1
        assert report.unmapped_count == 3
        assert not report.is_empty()

    def test_folds_in_favorites_mismatches_and_errors(self) -> None:
        favorites_outcome = FavoritesOutcome(mismatched=[(10, 20)], errors=[(10, "toggle failed")])
        report = build_report({"anime": SyncOutcome()}, {"anime": favorites_outcome})

        assert report.favorites_mismatched == [("anime", 10, 20)]
        assert report.favorites_errors == [("anime", 10, "toggle failed")]
        assert not report.is_empty()


class TestFormatReport:
    def test_empty_report_says_so(self) -> None:
        assert format_report(SyncReport()) == "No warnings, conflicts, or mismatches."

    def test_includes_each_populated_section(self) -> None:
        report = SyncReport(
            warnings=["some warning"],
            conflicts=[_conflict()],
            unmapped_count=2,
            favorites_mismatched=[("anime", 10, 20)],
            favorites_errors=[("anime", 10, "toggle failed")],
        )
        text = format_report(report)

        assert "Warnings (1):" in text
        assert "some warning" in text
        assert "Conflicts (1):" in text
        assert "'Loser'" in text and "'Winner'" in text
        assert "Unmapped entries: 2" in text
        assert "Favorites missing on MAL (1):" in text
        assert "AniList ID 10 (MAL ID 20)" in text
        assert "Favorites errors (1):" in text
        assert "toggle failed" in text

    def test_omits_sections_with_nothing_to_report(self) -> None:
        report = SyncReport(unmapped_count=1)
        text = format_report(report)
        assert "Warnings" not in text
        assert "Conflicts" not in text
        assert "Unmapped entries: 1" in text
