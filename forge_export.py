"""Export archived Arena decklists to Forge's .dck format.

Reads every deck_submission across the archive, dedupes identical lists, and
writes one .dck per unique deck to forge_decks/. Drop those files into Forge's
deck folder (Constructed decks live under its "decks/constructed" data dir) to
play them against the AI.

Card names come from scryfall_cache.json, so run this after run.sh. Double-faced
and adventure cards are written by their front face, which is how Forge indexes
them. Unresolved ids (Alchemy-only cards Forge doesn't have) are skipped with a
warning rather than producing a card Forge can't load.
"""

import collections
import glob
import hashlib
import json
import pathlib
import re

CACHE = json.load(open("scryfall_cache.json", encoding="utf8"))
OUTDIR = pathlib.Path("forge_decks")


def name_of(arena_id):
    entry = CACHE.get(str(arena_id)) or {}
    name = entry.get("name")
    # Front face only: Forge indexes DFCs/adventures by their first face.
    return name.split(" // ")[0] if name else None


def counted_lines(card_ids):
    lines, skipped = [], []
    counts = collections.Counter(card_ids or [])
    for arena_id, count in counts.items():
        name = name_of(arena_id)
        if name:
            lines.append((count, name))
        else:
            skipped.append((count, arena_id))
    lines.sort(key=lambda cn: (-cn[0], cn[1]))
    return lines, skipped


def main():
    decks = {}  # (maindeck, sideboard) multiset key -> latest submission
    for path in sorted(glob.glob("archive/*/deck_submission.jsonl")):
        for line in open(path, encoding="utf8"):
            if not line.strip():
                continue
            blob = json.loads(line)
            key = (
                tuple(sorted(blob.get("maindeck_card_ids") or [])),
                tuple(sorted(blob.get("sideboard_card_ids") or [])),
            )
            decks[key] = blob  # later snapshots win; name edits carry over

    OUTDIR.mkdir(exist_ok=True)
    for key, blob in sorted(decks.items(), key=lambda kv: kv[1].get("time", "")):
        summary = (blob.get("payload") or {}).get("Summary") or {}
        deck_name = summary.get("Name") or blob.get("event_name") or "Unknown"

        main_lines, main_skipped = counted_lines(blob.get("maindeck_card_ids"))
        side_lines, side_skipped = counted_lines(blob.get("sideboard_card_ids"))

        digest = hashlib.sha256(repr(key).encode()).hexdigest()[:6]
        safe = re.sub(r"[^A-Za-z0-9]+", "_", deck_name).strip("_") or "deck"
        out = OUTDIR / f"{safe}_{digest}.dck"

        with open(out, "w", encoding="utf8") as handle:
            handle.write(f"[metadata]\nName={deck_name}\n")
            handle.write("[Main]\n")
            for count, name in main_lines:
                handle.write(f"{count} {name}\n")
            handle.write("[Sideboard]\n")
            for count, name in side_lines:
                handle.write(f"{count} {name}\n")

        total = sum(c for c, _ in main_lines)
        print(f"{out}  ({deck_name}: {total} main / {sum(c for c, _ in side_lines)} side)")
        for count, arena_id in main_skipped + side_skipped:
            print(f"  WARNING: skipped {count}x unresolved <arena:{arena_id}>")

    print(f"\n{len(decks)} unique decks -> {OUTDIR.resolve()}")


if __name__ == "__main__":
    main()