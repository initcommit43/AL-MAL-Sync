"""Score normalization between AniList (100-pt) and MAL (10-pt) scales.

Ported from the reference Go tool's score.go. AniList's score meaning depends on
the user's chosen score format; MAL is always a plain 0-10 integer.
"""

from __future__ import annotations

POINT_100 = "POINT_100"
POINT_10 = "POINT_10"
POINT_10_DECIMAL = "POINT_10_DECIMAL"
POINT_5 = "POINT_5"
POINT_3 = "POINT_3"


def _round_half_up(value: float) -> int:
    # Python's round() uses banker's rounding (round-half-to-even): round(8.5) == 8.
    # We want round-half-up (4.5->5, 8.5->9, 9.5->10) to match the reference tool's
    # int(x + 0.5) behavior. Using round() here would silently disagree with it on
    # every .5 case.
    return int(value + 0.5)


def normalize_score_for_mal(anilist_score: float, score_format: str) -> int:
    """Convert an AniList score to MAL's 0-10 integer scale."""
    if anilist_score == 0:
        return 0

    if score_format == POINT_100:
        normalized = anilist_score / 10.0
    elif score_format in (POINT_10, POINT_10_DECIMAL):
        normalized = anilist_score
    elif score_format == POINT_5:
        normalized = anilist_score * 2.0
    elif score_format == POINT_3:
        normalized = (anilist_score / 3.0) * 10.0
    else:
        normalized = anilist_score / 10.0 if anilist_score > 10 else anilist_score

    normalized = max(0.0, min(10.0, normalized))
    return _round_half_up(normalized)


def denormalize_score_for_anilist(normalized_score: int, score_format: str) -> int:
    """Convert a MAL 0-10 integer score back to AniList's score format."""
    if normalized_score == 0:
        return 0

    if score_format == POINT_100:
        result = normalized_score * 10.0
    elif score_format in (POINT_10, POINT_10_DECIMAL):
        result = float(normalized_score)
    elif score_format == POINT_5:
        result = normalized_score / 2.0
    elif score_format == POINT_3:
        result = (normalized_score / 10.0) * 3.0
        result = max(result, 1.0)  # normalized_score > 0 here, so never round down to 0
    else:
        result = float(normalized_score)

    return _round_half_up(result)
