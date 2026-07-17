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
```

Key `sync`/`watch` flags: `--manga` (manga instead of anime), `--all` (both),
`--reverse-direction` (MyAnimeList -> AniList instead of the default),
`--force` (skip matching, sync by id directly), `--dry-run`, `--offline-db`/
`--arm-api`/`--jikan-api` (opt in to extra id-mapping sources), `--favorites`.
Run `al-mal-sync <command> --help` for the full list.

See [docs/date-sync.md](docs/date-sync.md) for the exact date-comparison
rules applied during sync.

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

## Credits

- [bigspawn/anilist-mal-sync](https://github.com/bigspawn/anilist-mal-sync) —
  the Go reference this project's sync logic is ported from.
- [manami-project/anime-offline-database](https://github.com/manami-project/anime-offline-database),
  [Hato](https://hato.malupdaterosx.moe), [Jikan](https://jikan.moe), and
  [ARM](https://arm.haglund.dev) — the id-mapping data sources that make
  automatic AniList<->MAL matching possible.
