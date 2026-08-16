"""Resolve MTGA grpIds to card names via Scryfall's /cards/arena/:id endpoint.

Results are cached to disk, so reruns cost no requests. Only the fields that
actually carry card ids are scanned - other ints in the blobs are counters.

Besides names, the cache stores per-format legalities and release dates for
the coach layer (deck_health.py). Legality is volatile: after a ban
announcement, rerun with --refresh or cached entries keep saying "legal".

Scryfall publishes no official OpenAPI spec and blocks doc scraping; the
sanctioned bulk alternative (default_cards, ~558MB) is only worth it if the
unique id count runs to thousands. A typical few sessions is ~100 ids.
"""

import argparse
import glob
import json
import pathlib
import time
import urllib.error
import urllib.request

# Fields whose values are card ids (flat lists, or lists of lists).
CARD_FIELDS = (
    "maindeck_card_ids",
    "sideboard_card_ids",
    "opening_hand",
    "drawn_cards",
    "drawn_hands",
    "opponent_card_ids",
    "mulligans",
)

# Scryfall asks for 50-100ms between requests.
REQUEST_DELAY = 0.1
USER_AGENT = "mtga-log-client-local/0.1 (personal log analysis)"

# Formats the coach reasons about; Scryfall's full legality map covers ~20.
ARENA_FORMATS = ("standard", "alchemy", "historic", "timeless", "explorer", "brawl")


def flatten_ids(value) -> list:
    if isinstance(value, int):
        return [value]
    if isinstance(value, list):
        return [i for v in value for i in flatten_ids(v)]
    return []


def collect_ids(pattern: str) -> set:
    ids = set()
    for path in sorted(glob.glob(pattern)):
        for line in open(path, encoding="utf8"):
            if not line.strip():
                continue
            blob = json.loads(line)
            for field in CARD_FIELDS:
                ids.update(flatten_ids(blob.get(field)))
    return ids


def fetch_name(arena_id: int) -> dict:
    request = urllib.request.Request(
        f"https://api.scryfall.com/cards/arena/{arena_id}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        card = json.load(response)
    legalities = card.get("legalities") or {}
    return {
        "name": card.get("name"),
        "mana_cost": card.get("mana_cost"),
        "type_line": card.get("type_line"),
        "set": card.get("set"),
        "rarity": card.get("rarity"),
        "cmc": card.get("cmc"),
        "released_at": card.get("released_at"),
        "legalities": {f: legalities.get(f) for f in ARENA_FORMATS},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--glob", default="archive/*/*.jsonl", help="Input JSONL glob")
    parser.add_argument("--cache", default="scryfall_cache.json", help="Cache file")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Refetch every resolved entry. Legality is volatile — run this "
        "after a ban announcement or the cache keeps saying 'legal'.",
    )
    args = parser.parse_args()

    cache_path = pathlib.Path(args.cache)
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}

    ids = collect_ids(args.glob)
    new = sorted(i for i in ids if str(i) not in cache)
    # Entries from before legality caching existed (or a forced refresh).
    # 404-cached ids (name: null) are never retried either way.
    stale = sorted(
        int(k)
        for k, v in cache.items()
        if v.get("name") and (args.refresh or "legalities" not in v)
    )
    missing = new + stale
    print(
        f"{len(ids)} unique card ids, {len(cache)} cached, "
        f"{len(new)} new, {len(stale)} stale -> {len(missing)} to fetch"
    )

    failures = 0
    for index, arena_id in enumerate(missing, 1):
        try:
            cache[str(arena_id)] = fetch_name(arena_id)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                # 404 = Arena-only object (token, emblem, Alchemy rebalance) that
                # Scryfall does not index under this arena_id. Record and move on.
                cache[str(arena_id)] = {"name": None, "error": error.code}
            else:
                # Transient (429/5xx). A --refresh feeds resolved entries through
                # here — never overwrite one; unresolved ids retry next run.
                print(f"  ! HTTP {error.code} on {arena_id}, keeping cached entry")
            failures += 1
        except urllib.error.URLError as error:
            print(f"  ! network error on {arena_id} ({error.reason}), keeping cached entry")
            failures += 1
        if index % 25 == 0 or index == len(missing):
            print(f"  {index}/{len(missing)}")
            cache_path.write_text(json.dumps(cache, indent=1, sort_keys=True))
        time.sleep(REQUEST_DELAY)

    cache_path.write_text(json.dumps(cache, indent=1, sort_keys=True))
    resolved = sum(1 for v in cache.values() if v.get("name"))
    print(f"\ncache: {resolved}/{len(cache)} resolved, {failures} unresolved this run")
    print(f"-> {cache_path.resolve()}")


if __name__ == "__main__":
    main()