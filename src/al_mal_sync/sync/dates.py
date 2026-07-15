"""Start/finish date sync rules (see docs/date-sync.md).

Ported from the reference Go tool's sameDates()/parseDateOrNow() (anime.go).
AniList's own fuzzy-date (year/month/day) to date conversion lives on
AniListDate.to_date() in clients/anilist.py since it's specific to that API's
response shape; this module covers the comparison rule and MAL's date string.
"""

from __future__ import annotations

from datetime import date, datetime

MAL_DATE_FORMAT = "%Y-%m-%d"


def same_dates(source: date | None, target: date | None) -> bool:
    """True if the target doesn't need to be updated to match the source.

    A missing source date is always treated as "same", even against a target
    that has one set, so a nil/unknown source date never clears a date the user
    already set on the target service.
    """
    if source is None:
        return True
    if target is None:
        return False
    return source == target


def parse_mal_date(value: str) -> date | None:
    """Parse MAL's "YYYY-MM-DD" list-status date string.

    Returns None for an empty string or anything that doesn't match that exact
    format (MAL sometimes stores partial dates like "2020" for older entries,
    which we treat the same as "no date" rather than guessing month/day).
    """
    if not value:
        return None
    try:
        return datetime.strptime(value, MAL_DATE_FORMAT).date()
    except ValueError:
        return None
