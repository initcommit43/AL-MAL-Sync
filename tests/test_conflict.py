"""Tests for duplicate-target resolution: priority ordering by strategy_idx,
title tie-break, and conflict recording for the losers."""

from __future__ import annotations

from al_mal_sync.mapping.strategies import MatchResult
from al_mal_sync.models import Anime
from al_mal_sync.sync.conflict import resolve_duplicates


def _anime(**overrides) -> Anime:
    defaults = {"id_mal": 1, "id_anilist": 1, "title_en": "Show"}
    defaults.update(overrides)
    return Anime(**defaults)


class TestResolveDuplicates:
    def test_single_claimant_is_resolved_without_conflict(self) -> None:
        target = _anime(id_mal=5)
        source = _anime(id_anilist=1)
        matches = [(source, MatchResult(target=target, strategy_name="IDStrategy", strategy_idx=1))]

        resolved, conflicts = resolve_duplicates(matches)

        assert len(resolved) == 1
        assert resolved[0].source is source
        assert conflicts == []

    def test_lower_strategy_idx_wins(self) -> None:
        target = _anime(id_mal=5)
        winner = _anime(id_anilist=1, title_en="Winner")
        loser = _anime(id_anilist=2, title_en="Loser")
        matches = [
            (loser, MatchResult(target=target, strategy_name="TitleStrategy", strategy_idx=5)),
            (winner, MatchResult(target=target, strategy_name="IDStrategy", strategy_idx=1)),
        ]

        resolved, conflicts = resolve_duplicates(matches)

        assert len(resolved) == 1
        assert resolved[0].source is winner
        assert len(conflicts) == 1
        assert conflicts[0].source is loser
        assert conflicts[0].winner_source is winner
        assert conflicts[0].target_id == 5

    def test_exact_title_match_breaks_tie_on_equal_priority(self) -> None:
        target = _anime(id_mal=5, title_en="Exact Show")
        exact = _anime(id_anilist=1, title_en="Exact Show")
        fuzzy = _anime(id_anilist=2, title_en="Close Show")
        matches = [
            (fuzzy, MatchResult(target=target, strategy_name="TitleStrategy", strategy_idx=3)),
            (exact, MatchResult(target=target, strategy_name="TitleStrategy", strategy_idx=3)),
        ]

        resolved, conflicts = resolve_duplicates(matches)

        assert resolved[0].source is exact
        assert conflicts[0].source is fuzzy

    def test_stable_order_when_fully_tied(self) -> None:
        target = _anime(id_mal=5, title_en="Ambiguous")
        first = _anime(id_anilist=1, title_en="Other A")
        second = _anime(id_anilist=2, title_en="Other B")
        matches = [
            (first, MatchResult(target=target, strategy_name="TitleStrategy", strategy_idx=3)),
            (second, MatchResult(target=target, strategy_name="TitleStrategy", strategy_idx=3)),
        ]

        resolved, _ = resolve_duplicates(matches)

        assert resolved[0].source is first

    def test_independent_targets_all_resolved(self) -> None:
        target_a = _anime(id_mal=1)
        target_b = _anime(id_mal=2)
        source_a = _anime(id_anilist=1)
        source_b = _anime(id_anilist=2)
        matches = [
            (source_a, MatchResult(target=target_a, strategy_name="IDStrategy", strategy_idx=1)),
            (source_b, MatchResult(target=target_b, strategy_name="IDStrategy", strategy_idx=1)),
        ]

        resolved, conflicts = resolve_duplicates(matches)

        assert len(resolved) == 2
        assert conflicts == []
