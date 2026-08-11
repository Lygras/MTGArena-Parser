"""Print readable game summaries and decklists using the Scryfall name cache."""

import collections
import glob
import json

CACHE = json.load(open("scryfall_cache.json", encoding="utf8"))


def name_of(arena_id) -> str:
    entry = CACHE.get(str(arena_id)) or {}
    # Unresolved ids stay visible rather than being silently dropped.
    return entry.get("name") or f"<arena:{arena_id}>"


def decklist(card_ids) -> str:
    counts = collections.Counter(name_of(i) for i in card_ids or [])
    return "\n".join(f"    {n}x {name}" for name, n in counts.most_common())


# Snapshots overlap: a growing Player.log is re-archived, and yesterday's
# games reappear in it. Later snapshots win (sorted dir names are chronological).
games_by_key = {}
for path in sorted(glob.glob("archive/*/game_result.jsonl")):
    for line in open(path, encoding="utf8"):
        if line.strip():
            game = json.loads(line)
            key = (game.get("match_id") or game.get("utc_time"), game.get("game_number"))
            games_by_key[key] = game

games = sorted(games_by_key.values(), key=lambda g: g.get("time", ""))

for game in games:
    result = "WON " if game.get("won") else "LOST"
    print(f"\n{'=' * 70}")
    print(
        f"{game.get('time', '?')[:19]}  {game.get('event_name')}  "
        f"{result}  turns={game.get('turns')}  "
        f"on_play={game.get('on_play')}  mulls={game.get('mulligan_count')}"
    )
    print(f"  end: {game.get('game_end_reason')}")

    print("\n  Opening hand:")
    print(decklist(game.get("opening_hand")))

    opponent = game.get("opponent_card_ids")
    if opponent:
        print("\n  Opponent cards seen:")
        print(decklist(opponent))

    print(f"\n  Maindeck ({len(game.get('maindeck_card_ids') or [])}):")
    print(decklist(game.get("maindeck_card_ids")))