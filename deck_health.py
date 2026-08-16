"""Per-archetype deck health across formats and regime changes (DESIGN.md Phase 1).

Clusters your exact decklists into fuzzy archetypes (component A — so winrate
accumulates across tinkering instead of shattering), splits each archetype's
record per format and match type, segments it at the dated regime changes in
regime_changes.json, and prints records with Wilson 95% intervals (component
D). Archetypes holding banned/rotated cards are flagged dead in that format,
with a pointer to formats where the same deck still lives.

Reads archive/*/game_result.jsonl + deck_submission.jsonl + scryfall_cache.json.
Run resolve_cards.py first — with --refresh after a ban announcement, or the
cached legalities lie. Everything printed describes YOUR games only.
"""

import argparse
import collections
import json
import pathlib

import coachlib


def pct(x):
    return f"{100 * x:.0f}%"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--games", default="archive/*/game_result.jsonl", help="game_result glob")
    ap.add_argument("--decks", default="archive/*/deck_submission.jsonl", help="deck_submission glob")
    ap.add_argument("--cache", default="scryfall_cache.json")
    ap.add_argument("--events", default="event_formats.json")
    ap.add_argument("--regimes", default="regime_changes.json")
    ap.add_argument(
        "--threshold", type=float, default=0.7,
        help="multiset-Jaccard for same-archetype (open question, DESIGN.md §13)",
    )
    args = ap.parse_args()

    cache = json.loads(pathlib.Path(args.cache).read_text(encoding="utf8"))
    events = coachlib.load_data_file(args.events, {"exact": {}, "substring": []})
    regimes = coachlib.load_data_file(args.regimes, {"changes": []}).get("changes", [])

    def name_of(arena_id):
        entry = cache.get(str(arena_id)) or {}
        name = entry.get("name")
        # Front face: regime_changes.json and bans name cards by front face.
        return name.split(" // ")[0] if name else f"<arena:{arena_id}>"

    def legality(arena_id, fmt):
        entry = cache.get(str(arena_id)) or {}
        return (entry.get("legalities") or {}).get(fmt)

    games = coachlib.load_games(args.games)

    # Deck names by exact-list key; later submissions win (name edits carry).
    deck_names = {}
    for blob in coachlib.load_jsonl(args.decks):
        summary = (blob.get("payload") or {}).get("Summary") or {}
        key = tuple(sorted(blob.get("maindeck_card_ids") or []))
        if key and summary.get("Name"):
            deck_names[key] = summary["Name"]

    # Annotate games; cluster lists most-played-first for stable anchors.
    plays_per_list = collections.Counter()
    for game in games:
        game["_list"] = tuple(sorted(game.get("maindeck_card_ids") or []))
        game["_ctx"] = coachlib.event_context(game.get("event_name"), events)
        game["_flag"] = coachlib.hygiene_flag(game)
        if game["_list"]:
            plays_per_list[game["_list"]] += 1

    clusters = coachlib.cluster_lists(
        [k for k, _ in plays_per_list.most_common()], args.threshold
    )
    list_to_cluster = {}
    for cluster in clusters:
        for key in cluster["members"]:
            list_to_cluster[key] = cluster
        names = collections.Counter(
            deck_names[k] for k in cluster["members"] if k in deck_names
        )
        four_ofs = [i for i, n in cluster["anchor"].items() if n >= 4]
        cluster["name"] = (
            names.most_common(1)[0][0] if names
            else f"{name_of(four_ofs[0])} deck" if four_ofs
            else "unnamed archetype"
        )
        cluster["cards"] = sorted(set().union(*(set(k) for k in cluster["members"])))
        cluster["by_format"] = collections.defaultdict(list)

    excluded, unknown_events = [], collections.Counter()
    for game in games:
        if not game["_ctx"]["known"]:
            unknown_events[game.get("event_name") or "?"] += 1
        cluster = list_to_cluster.get(game["_list"])
        if cluster is None:
            continue  # game_result without a decklist
        if game["_flag"]:
            excluded.append(game)
            continue
        cluster["by_format"][game["_ctx"]["format"]].append(game)

    print("DECK HEALTH — your logged games only. Every line names the population it")
    print("describes (format, match type, date segment); nothing here is 'the meta'.")
    print("Wilson 95% intervals: a dated ban is decisive, a small sample is not.")

    for cluster in sorted(
        clusters, key=lambda c: -sum(len(v) for v in c["by_format"].values())
    ):
        total = sum(len(v) for v in cluster["by_format"].values())
        if not total:
            continue
        print(
            f"\n=== {cluster['name']}  "
            f"({len(cluster['members'])} list variant(s), {total} games) ==="
        )
        arch_names = {name_of(i) for i in cluster["cards"]}

        for fmt, fmt_games in sorted(
            cluster["by_format"].items(), key=lambda kv: -len(kv[1])
        ):
            # Regime changes in this format touching this archetype's cards
            # (rotations apply to every deck in the format).
            changes = [
                dict(ch, hits=sorted(set(ch.get("cards", [])) & arch_names))
                for ch in regimes
                if ch.get("format") == fmt
            ]
            changes = [ch for ch in changes if ch["hits"] or ch.get("kind") == "rotation"]

            boundaries = sorted({ch["date"] for ch in changes})
            for lo_date, hi_date, label in coachlib.segment_bounds(boundaries):
                seg = [
                    g for g in fmt_games
                    if lo_date <= ((g.get("time") or "")[:10] or "0000") < hi_date
                ]
                if not seg:
                    continue
                # Bo1's hand smoother makes Bo1/Bo3 different populations.
                by_type = coachlib.group_by(seg, lambda g: g["_ctx"]["match_type"])
                for match_type, mt_games in sorted(by_type.items()):
                    wins = sum(1 for g in mt_games if g.get("won"))
                    n = len(mt_games)
                    lo, hi = coachlib.wilson(wins, n)
                    small = "  ⚠ small sample" if n < 20 else ""
                    print(
                        f"  {fmt:<9} {match_type:<4} {label:<16} "
                        f"{wins}W–{n - wins}L  {pct(wins / n)}  "
                        f"CI[{pct(lo)}–{pct(hi)}]  ({n}g){small}"
                    )

            for ch in changes:
                what = ", ".join(ch["hits"]) or ch.get("kind", "change")
                print(f"    ⚠ regime change {ch['date']} ({ch.get('kind', '?')}): {what}")

            dead = [
                (i, legality(i, fmt))
                for i in cluster["cards"]
                if legality(i, fmt) in ("banned", "not_legal")
            ]
            for arena_id, status in dead:
                copies = cluster["anchor"].get(arena_id, "?")
                print(
                    f"    ✗ {fmt}: lost {copies}x {name_of(arena_id)} ({status}) — "
                    f"pre-change results describe a deck that no longer exists"
                )
            if dead:
                for alt_fmt, alt_games in sorted(
                    cluster["by_format"].items(), key=lambda kv: -len(kv[1])
                ):
                    # "unknown" is a quarantine bucket, not a queueable format,
                    # and legality there is unknowable — never suggest it.
                    if alt_fmt in (fmt, "unknown") or any(
                        legality(i, alt_fmt) in ("banned", "not_legal")
                        for i in cluster["cards"]
                    ):
                        continue
                    wins = sum(1 for g in alt_games if g.get("won"))
                    n = len(alt_games)
                    lo, hi = coachlib.wilson(wins, n)
                    print(
                        f"    → still alive in {alt_fmt}: "
                        f"{pct(wins / n)} CI[{pct(lo)}–{pct(hi)}] ({n}g)"
                    )

    if excluded:
        print(f"\n{len(excluded)} game(s) excluded as non-games:")
        for game in excluded:
            print(
                f"  - {(game.get('time') or '?')[:16]}  "
                f"{game.get('event_name')}: {game['_flag']}"
            )
    if unknown_events:
        print("\n⚠ event names event_formats.json cannot classify (add them there):")
        for name, n in unknown_events.most_common():
            print(f"  - {name} ({n} games)")


if __name__ == "__main__":
    main()