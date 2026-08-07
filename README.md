# AL-MAL-Sync

Bidirectional sync tool between [AniList](https://anilist.co) and
[MyAnimeList](https://myanimelist.net), written in Python.

Ported from [bigspawn/anilist-mal-sync](https://github.com/bigspawn/anilist-mal-sync)
(Go) — that project was studied as the reference for tricky sync logic
(id-mapping strategy order, score normalization, date-sync edge cases,
favorites asymmetry), not translated line-for-line.

## Features

- Syncs anime and/or manga progress, status, score, and start/finish dates
  between AniList and MyAnimeList, in either direction.
- Matches entries across services through a chain of id-mapping strategies —
  manual overrides, direct id, the
  [anime-offline-database](https://github.com/manami-project/anime-offline-database),
  [Hato](https://hato.malupdaterosx.moe), [ARM](https://arm.haglund.dev), title
  matching, [Jikan](https://jikan.moe), and a last-resort live API search — so
  most entries match automatically without manual mapping.
- Favorites sync (see [docs/favorites-sync.md](docs/favorites-sync.md) for why
  it's one-directional).
- Tracks entries that couldn't be matched and lets you resolve them
  interactively (`al-mal-sync unmapped --fix`) instead of silently dropping
  them.
- One-shot sync, or `watch` mode on an interval or cron schedule.
- Per-run statistics table plus a report of warnings, duplicate-match
  conflicts, and favorites mismatches.
- A desktop GUI (PySide6) for everything above, if you'd rather click
  through tabs than remember flags — see [GUI](#gui) below.

## Installation

Not yet published to PyPI. Install from source:

```sh
git clone https://github.com/initcommit43/AL-MAL-Sync.git
cd AL-MAL-Sync
pip install -e .
```

Or run it in Docker — see [Docker](#docker) below.

## Configuration

Settings load with priority: environment variable > `config.yaml` > built-in
default. You can use either, or mix both (e.g. credentials via env vars in a
container, everything else via `config.yaml`).

```sh
cp config.example.yaml config.yaml
# edit config.yaml with your AniList/MyAnimeList app credentials
```

At minimum you need an [AniList API client](https://anilist.co/settings/developer)
and a [MyAnimeList API client](https://myanimelist.net/apiconfig) — each gives
you a client id (and secret, for AniList) to put in `config.yaml` or the
matching env vars (`ANILIST_CLIENT_ID`/`ANILIST_USERNAME`,
`MAL_CLIENT_ID`/`MAL_USERNAME`, etc. — see `config.example.yaml` for the full
list of keys and their env var equivalents).

## Usage

```sh
al-mal-sync login              # authenticate with both services (opens a browser)
al-mal-sync status             # check auth status
al-mal-sync sync               # one-shot sync, anime, AniList -> MyAnimeList
al-mal-sync sync --all --dry-run --favorites   # anime + manga, preview only, plus favorites
al-mal-sync watch -i 6h        # sync every 6 hours
al-mal-sync watch -s "0 */6 * * *"  # or on a cron schedule instead
al-mal-sync unmapped --fix     # resolve entries that couldn't be auto-matched
al-mal-sync export -s anilist --all            # AniList's list -> MAL-format XML files
al-mal-sync import -i list.xml -t anilist      # import a MAL-format XML file into AniList
```

Key `sync`/`watch` flags: `--manga` (manga instead of anime), `--all` (both),
`--reverse-direction` (MyAnimeList -> AniList instead of the default),
`--force` (skip matching, sync by id directly), `--dry-run`, `--offline-db`/
`--arm-api`/`--jikan-api` (opt in to extra id-mapping sources), `--favorites`.
Run `al-mal-sync <command> --help` for the full list.

`export`/`import` read and write the same XML format myanimelist.net's own
"Export list" produces (and its importer, and AniList's list importer, both
accept) -- useful since AniList has no native list-export feature of its own.
`import` reuses the exact same id-mapping/matching pipeline as `sync`, so a
file with no AniList ids in it (e.g. one exported straight from MAL) still
matches existing entries by title/offline-db/Hato/ARM/Jikan, same as a live
MyAnimeList -> AniList sync would.

See [docs/date-sync.md](docs/date-sync.md) for the exact date-comparison
rules applied during sync.

## GUI

A desktop app covering the same commands above — Settings (edit
`config.yaml`), Login/Logout with live auth status, Sync (with a progress
bar and live log output), Watch (a non-blocking interval/cron loop, only
while the window is open), Unmapped (resolve entries interactively instead
of at a terminal prompt), Mappings (edit `mappings.yaml` in a table), and
Logs.

```sh
pip install -e ".[gui]"
al-mal-sync-gui
```

It shares the same `config.yaml`/`mappings.yaml`/token store as the CLI —
either one works against the same setup. For unattended background
scheduling, keep using `al-mal-sync watch` (CLI/Docker); the GUI's Watch
tab only runs while its window is open.

## Docker

```sh
cp docker-compose.example.yaml docker-compose.yaml
# edit the credentials/env vars in docker-compose.yaml
docker compose run --rm al-mal-sync al-mal-sync login   # one-time auth
docker compose up -d                                     # then run watch mode
```

The container persists `token.json`/`mappings.yaml`/caches under
`/config/al-mal-sync` — bind-mount `/config` (as the example compose file
does) to keep them across restarts. It honors `PUID`/`PGID` so files written
there are owned by your host user, not a container-only uid.

## Development

```sh
pip install -e ".[dev]"
ruff check .
pytest
```

GUI tests (`tests/test_gui_*.py`) skip themselves gracefully if PySide6
isn't installed; run `pip install -e ".[dev,gui]"` instead to include them.

## Credits

- [bigspawn/anilist-mal-sync](https://github.com/bigspawn/anilist-mal-sync) —
  the Go reference this project's sync logic is ported from.
- [manami-project/anime-offline-database](https://github.com/manami-project/anime-offline-database),
  [Hato](https://hato.malupdaterosx.moe), [Jikan](https://jikan.moe), and
  [ARM](https://arm.haglund.dev) — the id-mapping data sources that make
  automatic AniList<->MAL matching possible.

## License

[MIT](LICENSE)
