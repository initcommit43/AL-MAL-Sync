"""Load/save mappings.yaml: manual AniList<->MAL ID mappings and ignore rules.

Ported from the reference Go tool's mappings.go. One deliberate simplification:
the Go version hand-builds YAML nodes so each ignored ID gets an inline comment
(e.g. "12345  # Some Title : reason"). That's cosmetic and not worth the added
complexity here (a ruamel.yaml round-trip could bring it back later if wanted);
this version does a plain PyYAML load/dump instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..config import default_mappings_path


class MappingsError(Exception):
    """Raised when mappings.yaml exists but isn't structured as expected."""


@dataclass
class ManualMapping:
    anilist_id: int
    mal_id: int
    comment: str = ""


@dataclass
class IgnoreConfig:
    anilist_ids: list[int] = field(default_factory=list)
    mal_ids: list[int] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)


@dataclass
class MappingsConfig:
    manual_mappings: list[ManualMapping] = field(default_factory=list)
    ignore: IgnoreConfig = field(default_factory=IgnoreConfig)

    def get_manual_mal_id(self, anilist_id: int) -> int | None:
        for mapping in self.manual_mappings:
            if mapping.anilist_id == anilist_id:
                return mapping.mal_id
        return None

    def get_manual_anilist_id(self, mal_id: int) -> int | None:
        for mapping in self.manual_mappings:
            if mapping.mal_id == mal_id:
                return mapping.anilist_id
        return None

    def is_ignored(self, anilist_id: int, title: str) -> bool:
        if anilist_id in self.ignore.anilist_ids:
            return True
        lowered = title.lower()
        return any(t.lower() == lowered for t in self.ignore.titles)

    def is_ignored_by_mal_id(self, mal_id: int) -> bool:
        return mal_id in self.ignore.mal_ids

    def add_ignore_by_id(self, anilist_id: int) -> None:
        if anilist_id not in self.ignore.anilist_ids:
            self.ignore.anilist_ids.append(anilist_id)

    def add_ignore_by_mal_id(self, mal_id: int) -> None:
        if mal_id not in self.ignore.mal_ids:
            self.ignore.mal_ids.append(mal_id)

    def add_manual_mapping(self, anilist_id: int, mal_id: int, comment: str = "") -> None:
        for mapping in self.manual_mappings:
            if mapping.anilist_id == anilist_id:
                mapping.mal_id = mal_id
                mapping.comment = comment
                return
        self.manual_mappings.append(ManualMapping(anilist_id, mal_id, comment))

    def save(self, path: str | Path | None = None) -> None:
        file_path = Path(path) if path is not None else Path(default_mappings_path())
        file_path.parent.mkdir(parents=True, exist_ok=True)

        data: dict[str, Any] = {}
        if self.manual_mappings:
            data["manual_mappings"] = [
                {
                    "anilist_id": m.anilist_id,
                    "mal_id": m.mal_id,
                    **({"comment": m.comment} if m.comment else {}),
                }
                for m in self.manual_mappings
            ]

        ignore_data: dict[str, Any] = {}
        if self.ignore.anilist_ids:
            ignore_data["anilist_ids"] = self.ignore.anilist_ids
        if self.ignore.mal_ids:
            ignore_data["mal_ids"] = self.ignore.mal_ids
        if self.ignore.titles:
            ignore_data["titles"] = self.ignore.titles
        if ignore_data:
            data["ignore"] = ignore_data

        file_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def load_mappings(path: str | Path | None = None) -> MappingsConfig:
    """Load mappings from YAML. Missing file is not an error, just an empty config."""
    file_path = Path(path) if path is not None else Path(default_mappings_path())

    try:
        text = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return MappingsConfig()

    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise MappingsError(f"mappings file {file_path} must contain a YAML mapping")

    manual_mappings = [
        ManualMapping(
            anilist_id=item.get("anilist_id", 0),
            mal_id=item.get("mal_id", 0),
            comment=item.get("comment", "") or "",
        )
        for item in data.get("manual_mappings") or []
    ]

    ignore_data = data.get("ignore") or {}
    ignore = IgnoreConfig(
        anilist_ids=list(ignore_data.get("anilist_ids") or []),
        mal_ids=list(ignore_data.get("mal_ids") or []),
        titles=list(ignore_data.get("titles") or []),
    )

    return MappingsConfig(manual_mappings=manual_mappings, ignore=ignore)
