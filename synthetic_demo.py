"""Build a synthetic archive reproducing DESIGN.md §6's acceptance test.

Writes demo/ (gitignored): two overlapping snapshot dirs (exercising the
later-snapshot-wins dedupe) and a Scryfall-style cache. The key card is the
REAL Gran-Gran (arena id 97328, banned in Standard 2026-08-10 per
regime_changes.json); filler cards carry obviously fake "Demo ..." names so
synthetic output can never be mistaken for real data. Deterministic: no
randomness, no network.

    ./.venv/bin/python synthetic_demo.py
    ./.venv/bin/python deck_health.py --games 'demo/archive/*/game_result.jsonl' \
        --decks 'demo/archive/*/deck_submission.jsonl' --cache demo/scryfall_cache.json

Expected: Izzet Thunder strong pre-ban in Standard, collapsed after, still
alive in Timeless; the ban flagged as the regime boundary.
"""

import datetime
import itertools
import json
import pathlib

OUT = pathlib.Path("demo")
BAN_DATE = "2026-08-10"  # must match regime_changes.json

GRAN_GRAN = 97328  # real arena id; real legalities below
IZZET = [GRAN_GRAN] + list(range(900002, 900016))        # 15 distinct x4 = 60
IZZET_TWEAK = IZZET[:-1] + [900016]                      # tinkering: one swap
IZZET_POSTBAN = [900017] + IZZET[1:]                     # Gran-Gran replaced
RED = list(range(910001, 910016))

match_counter = itertools.count(1)


def flat(distinct):
    return sorted(i for i in distinct for _ in range(4))


def entry(name, standard="legal", timeless="legal"):
    return {
        "name": name,
        "released_at": "2025-11-21",
        "legalities": {
            "standard": standard,
            "alchemy": "legal",
            "historic": "legal",
            "timeless": timeless,
            "explorer": "legal",
            "brawl": "legal",
        },
    }


def dates(start, end, n):
    s, e = (datetime.date.fromisoformat(d) for d in (start, end))
    step = (e - s).days / max(n - 1, 1)
    return [str(s + datetime.timedelta(days=round(i * step))) for i in range(n)]


def winflags(n, wins):
    """Spread exactly `wins` wins evenly over n games (Bresenham, no RNG)."""
    flags, acc = [], 0
    for _ in range(n):
        acc += wins
        flags.append(acc >= n)
        if acc >= n:
            acc -= n
    return flags


def game(date, event, deck, won, turns=8, reason="Demo_Resolved"):
    i = next(match_counter)
    return {
        "time": f"{date}T19:{i % 60:02}:00",
        "match_id": f"demo-{i:04}",
        "game_number": 1,
        "event_name": event,
        "won": won,
        "turns": turns,
        "on_play": i % 2 == 0,
        "mulligan_count": 0,
        "game_end_reason": reason,
        "maindeck_card_ids": deck,
    }


def run(dates_, event, decks, wins):
    return [
        game(d, event, flat(decks[i % len(decks)]), w)
        for i, (d, w) in enumerate(zip(dates_, winflags(len(dates_), wins)))
    ]


def submission(name, deck, when):
    return {
        "time": f"{when}T18:00:00",
        "event_name": "Ladder",
        "maindeck_card_ids": flat(deck),
        "sideboard_card_ids": [],
        "payload": {"Summary": {"Name": name}},
    }


def main():
    cache = {str(GRAN_GRAN): entry("Gran-Gran", standard="banned")}
    for i in IZZET[1:] + [900016, 900017]:
        cache[str(i)] = entry(f"Demo Izzet {i % 100:02}")
    for i in RED:
        cache[str(i)] = entry(f"Demo Red {i % 100:02}")

    games = (
        # The acceptance-test story (DESIGN.md §6):
        run(dates("2026-06-20", "2026-08-09", 42), "Ladder", [IZZET, IZZET_TWEAK], 27)
        + run(dates(BAN_DATE, "2026-08-16", 11), "Ladder", [IZZET_POSTBAN], 4)
        + run(dates("2026-06-22", "2026-08-15", 30), "Timeless_Ladder", [IZZET], 19)
        # A healthy control deck:
        + run(dates("2026-07-01", "2026-08-14", 25), "Ladder", [RED], 14)
        # Hygiene fodder: instant scoops that must not count as losses.
        + [game("2026-07-15", "Ladder", flat(IZZET), False, turns=1,
                reason="Demo_Disconnect") for _ in range(3)]
        # An event name event_formats.json does not know.
        + [game("2026-07-20", "MidWeekMadness", flat(IZZET), True)]
    )
    games.sort(key=lambda g: g["time"])

    subs = [
        submission("Izzet Thunder", IZZET, "2026-06-20"),
        submission("Izzet Thunder", IZZET_TWEAK, "2026-07-05"),
        submission("Izzet Thunder", IZZET_POSTBAN, BAN_DATE),
        submission("Demo Red Aggro", RED, "2026-07-01"),
    ]

    # Two overlapping snapshots: the log grew, both were archived — the
    # dedupe in coachlib.load_games must collapse them.
    snapshots = {
        "20260720_demo1_aaaaaaaa": [g for g in games if g["time"][:10] < "2026-07-20"],
        "20260816_demo2_bbbbbbbb": games,
    }
    for dirname, snap_games in snapshots.items():
        snap = OUT / "archive" / dirname
        snap.mkdir(parents=True, exist_ok=True)
        with open(snap / "game_result.jsonl", "w", encoding="utf8") as fh:
            fh.writelines(json.dumps(g) + "\n" for g in snap_games)
        with open(snap / "deck_submission.jsonl", "w", encoding="utf8") as fh:
            fh.writelines(json.dumps(s) + "\n" for s in subs)

    (OUT / "scryfall_cache.json").write_text(
        json.dumps(cache, indent=1, sort_keys=True), encoding="utf8"
    )
    print(f"{len(games)} games across {len(snapshots)} overlapping snapshots -> {OUT}/")
    print("Now run:\n  ./.venv/bin/python deck_health.py"
          " --games 'demo/archive/*/game_result.jsonl'"
          " --decks 'demo/archive/*/deck_submission.jsonl'"
          " --cache demo/scryfall_cache.json")


if __name__ == "__main__":
    main()