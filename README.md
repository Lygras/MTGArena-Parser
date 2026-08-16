# mtga-parse

Parse MTG Arena logs **locally** into readable game data and decklists.
Nothing about your games is uploaded anywhere.

## Why this exists

[`mtga-log-client`](https://github.com/rconroy293/mtga-log-client) is the
17Lands uploader. It has no local-output mode — parsing a log and POSTing it to
`api.17lands.com` are the same code path. These scripts reuse its (very good)
log parser while replacing the network layer, so the parsed events land on disk
instead.

## Setup

Needs a checkout of the upstream client for its parser:

```bash
git clone https://github.com/rconroy293/mtga-log-client ~/mtga-log-client
./setup.sh
```

If your checkout is elsewhere, set `MTGA_CLIENT_SRC` to its `src/python` dir.
The upstream repo's `pyproject.toml` assumes `uv`; you don't need it — these
scripts only require `requests` and `python-dateutil`, which `setup.sh`
installs into a local `.venv`.

## Usage

```bash
./run.sh              # snapshot logs into archive/, resolve names, write report.txt
```

Each run content-hashes both Arena logs and archives any it hasn't seen into
`archive/<log-mtime>_<label>_<hash8>/` — one JSONL per event type plus the raw
log gzipped (set `MTGA_KEEP_RAW=0` to skip the gzip). Unchanged logs are
skipped, and rotation (`Player.log` becoming `Player-prev.log`) is caught by
the hash, so nothing is archived twice. `report.txt` covers every snapshot,
deduping games by `(match_id, game_number)`.

It runs hourly via cron (`crontab -l`), logging to `archive/cron.log`. Hourly
rather than daily because Arena only keeps two logs — a daily run can lose a
session on a 3-session day. WSL caveat: cron only fires while WSL is running.

Or step by step:

```bash
./.venv/bin/python local_parse.py "/mnt/c/.../MTGA/Player.log" -o archive/some-dir
./.venv/bin/python resolve_cards.py
./.venv/bin/python report.py > report.txt
```

### Forge export

```bash
./.venv/bin/python forge_export.py   # unique decklists -> forge_decks/*.dck
```

Copy the `.dck` files into Forge's constructed deck folder
(`%APPDATA%\Forge\decks\constructed` on Windows, `~/.forge/decks/constructed`
on Linux) to play your Arena decks against Forge's AI. DFC/adventure cards are
written by front face (how Forge indexes them); unresolved Alchemy-only ids
are skipped with a warning.

### Deck health

```bash
./.venv/bin/python deck_health.py    # archetype winrates by format & date segment
```

Clusters your decklists into fuzzy archetypes (so stats survive tinkering),
splits records per format, segments them at the dated bans/rotations in
`regime_changes.json`, and prints Wilson-interval winrates — flagging decks
that lost a card to a ban and where they still live. Append new B&R
announcements to `regime_changes.json`, map new Arena queue names in
`event_formats.json`, and rerun `resolve_cards.py --refresh` after bans so
cached legalities stay honest. `synthetic_demo.py` builds a fixture archive in
`demo/` to try it without real logs. The larger plan lives in `DESIGN.md`.

Set `MTGA_LOG_DIR` to override the log location. On Windows the logs live at
`%USERPROFILE%\AppData\LocalLow\Wizards Of The Coast\MTGA\`, reachable from WSL
under `/mnt/c/...`. `Player-prev.log` holds the previous session.

**Arena must have "Detailed Logs (Plugin Support)" enabled** (Settings →
Account) or the logs won't contain hands, decklists, or draft picks.

## How it works

| script | does |
|---|---|
| `local_parse.py` | Log → one JSONL per event type. Swaps `Follower._api_client` for a recorder that writes to disk. No `submit_*` return value is used upstream, so returning `None` is safe. Host is set to `localhost.invalid` as a second layer of defence. `token`/`client_version` are stripped; `submit_error_info` (crash telemetry) is dropped. |
| `resolve_cards.py` | Arena grpIds → card names via Scryfall, cached to `scryfall_cache.json` so reruns cost no requests. |
| `report.py` | Games, opening hands, opponent cards and decklists → stdout. |

### Event types produced

`game_result` is the interesting one — it carries opening hand, every card
drawn, mulligan counts, opponent's revealed cards, your full decklist and rank
data. Also emitted: `deck_submission`, `joined_event`, `ongoing_events`,
`player_progress`, `rank`, `user`.

### On card names

Card ids in the logs are Arena `grpId`s, not names. Scryfall's
`/cards/arena/:id` endpoint maps them.

- Scryfall publishes **no official OpenAPI/Swagger spec**, and its docs site
  403s automated agents. Unofficial community specs exist
  ([smgoller/scryfall-openapi](https://github.com/smgoller/scryfall-openapi))
  but describe endpoints you already know.
- The sanctioned bulk alternative is the `default_cards` dump (~558MB — it's
  the smallest file with `arena_id` on every print; `oracle_cards` only has it
  for whichever print it picks). Only worth it for thousands of ids, e.g. if
  you parse the `collection` blob, which holds your entire card pool.
- For a few sessions (~100 ids) the per-card endpoint is far lighter. Requests
  are spaced 100ms per Scryfall's guidance and send a `User-Agent`.

Some ids never resolve — Arena-only tokens, emblems and Alchemy rebalances that
Scryfall doesn't index under that `arena_id`. Those render as `<arena:ID>`
rather than being silently dropped.

## Notes

Uses the upstream parser, which is GNU GPL v3.0.

Unofficial Fan Content permitted under the Fan Content Policy. Not
approved/endorsed by Wizards. Portions of the materials used are property of
Wizards of the Coast. ©Wizards of the Coast LLC.