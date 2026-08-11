# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Local-only MTG Arena log parser. It reuses the parser from the upstream
[mtga-log-client](https://github.com/rconroy293/mtga-log-client) (17Lands
uploader) but replaces its network layer so parsed events land on disk instead
of being POSTed to api.17lands.com. Nothing about games is ever uploaded —
preserve that property in any change.

## Commands

```bash
./setup.sh                      # one-time: build .venv (requests, python-dateutil)
./run.sh                        # full pipeline: snapshot logs -> resolve names -> report.txt
```

There are no tests or linters. Always use the venv interpreter `./.venv/bin/python`,
not system python. Individual pipeline steps:

```bash
./.venv/bin/python local_parse.py <Player.log> -o archive/<dir>   # log -> JSONL per event type
./.venv/bin/python resolve_cards.py                               # grpIds -> names via Scryfall, cached
./.venv/bin/python report.py > report.txt                         # readable game summaries
./.venv/bin/python forge_export.py                                # unique decklists -> forge_decks/*.dck
```

## External dependency

`local_parse.py` imports `seventeenlands.mtga_follower` from a **separate
checkout** at `~/mtga-log-client/src/python` (override with `MTGA_CLIENT_SRC`).
That upstream code is GPL v3 and is not vendored here — don't copy it in.

## Architecture

Pipeline with the filesystem as the interface between stages:

1. **`run.sh`** — content-hashes both Arena logs (`Player.log`, `Player-prev.log`
   from `MTGA_LOG_DIR`), and for any hash not in `archive/seen_logs.txt` creates
   `archive/<log-mtime>_<label>_<hash8>/` via `local_parse.py`, plus a gzip of
   the raw log. Hash-based dedup means log rotation is handled and reruns are
   idempotent; the hash is recorded only after a successful parse so failures
   retry. Runs hourly via cron (`crontab -l`), logging to `archive/cron.log`.

2. **`local_parse.py`** — the core trick: instantiates the upstream `Follower`
   with an unroutable host (`localhost.invalid`), then swaps its `_api_client`
   for `LocalRecorder`, a duck-type of the upstream `ApiClient` whose
   `__getattr__` intercepts every `submit_<kind>(blob)` call and appends the
   blob to `<kind>.jsonl`. Safe because no upstream caller uses a `submit_*`
   return value. It strips `token`/`client_version` and drops
   `submit_error_info` (crash telemetry). If upstream adds new `submit_*`
   methods, they are captured automatically.

3. **`resolve_cards.py`** — collects Arena grpIds from the fields listed in
   `CARD_FIELDS` (other ints in the blobs are counters, not card ids) and
   resolves them via Scryfall's `/cards/arena/:id`, throttled to 100ms/request
   with a User-Agent. Results cache to `scryfall_cache.json`; 404s (Arena-only
   tokens, emblems, Alchemy rebalances) are cached as `{"name": null}` so they
   aren't refetched.

4. **`report.py` / `forge_export.py`** — read `archive/*/*.jsonl` plus the
   cache. Because a growing log gets re-archived, snapshots overlap: both
   scripts dedupe (games by `(match_id, game_number)`, decks by sorted
   main+side multiset) with **later snapshots winning**. Unresolved ids render
   as `<arena:ID>` in reports and are skipped with a warning in Forge exports —
   never silently dropped.

`game_result` is the richest event type (opening hand, draws, mulligans,
opponent's revealed cards, full decklist, rank). Others: `deck_submission`,
`joined_event`, `ongoing_events`, `player_progress`, `rank`, `user`.

## Environment variables

- `MTGA_CLIENT_SRC` — upstream checkout's `src/python` dir
- `MTGA_LOG_DIR` — Arena log directory (default is a WSL `/mnt/c/...` path)
- `MTGA_KEEP_RAW=0` — skip gzipping the raw log into snapshots