"""Shared plumbing for the coach layer (DESIGN.md, phases 0-1).

This module is the STATIONARY part: loading, deduping, clustering, confidence
intervals. Volatile facts live next door in dated JSON data files
(event_formats.json, regime_changes.json) per DESIGN.md §3 — when Arena adds a
queue or bans a card, extend those files, never this code.
"""

import collections
import glob
import json
import math
import pathlib


def load_data_file(path, fallback):
    path = pathlib.Path(path)
    return json.loads(path.read_text(encoding="utf8")) if path.exists() else fallback


def load_jsonl(pattern):
    for path in sorted(glob.glob(pattern)):
        for line in open(path, encoding="utf8"):
            if line.strip():
                yield json.loads(line)


def load_games(pattern):
    """Games across overlapping snapshots, deduped with later snapshots
    winning — same contract as report.py."""
    games = {}
    for game in load_jsonl(pattern):
        key = (game.get("match_id") or game.get("utc_time"), game.get("game_number"))
        games[key] = game
    return sorted(games.values(), key=lambda g: g.get("time", ""))


def event_context(event_name, table):
    """Map an Arena event name to {format, match_type, known}.

    Unknown events are not guessed at: they come back format="unknown" so
    downstream stats quarantine them (DESIGN.md §7: stamp every population).
    """
    name = event_name or ""
    exact = table.get("exact", {})
    if name in exact:
        entry = dict(exact[name])
        entry.setdefault("match_type", "bo1")
        entry["known"] = True
        return entry
    fmt, match_type = None, "bo1"
    for rule in table.get("substring", []):
        if rule["contains"].lower() in name.lower():
            fmt = rule.get("format", fmt)
            match_type = rule.get("match_type", match_type)
    return {"format": fmt or "unknown", "match_type": match_type, "known": fmt is not None}


def hygiene_flag(game):
    """Reason to exclude a game from winrate, or None if it counts.

    v1 heuristic: a "game" over by turn 2 was an early concede, disconnect or
    misclick — no Magic happened, and it poisons winrate and process signal
    alike (DESIGN.md component E).
    """
    turns = game.get("turns")
    if turns is not None and turns <= 2:
        return f"ended turn {turns} ({game.get('game_end_reason') or 'unknown reason'})"
    return None


def wilson(wins, games, z=1.96):
    """Wilson score interval for a winrate — honest on small samples
    (DESIGN.md component D). Returns (low, high)."""
    if not games:
        return 0.0, 1.0
    p = wins / games
    zz = z * z
    denom = 1 + zz / games
    centre = p + zz / (2 * games)
    spread = z * math.sqrt((p * (1 - p) + zz / (4 * games)) / games)
    return (centre - spread) / denom, (centre + spread) / denom


def multiset_jaccard(a, b):
    """Similarity of two Counter multisets: |intersection| / |union|."""
    union = sum((a | b).values())
    return sum((a & b).values()) / union if union else 0.0


def cluster_lists(list_keys, threshold=0.7):
    """Greedy fuzzy clustering of exact decklists into archetypes
    (DESIGN.md component A) so winrate accumulates across tinkering.

    list_keys: tuples of card ids (sorted maindeck multiset), most-played
    first — the first list seen anchors its cluster, so frequency order keeps
    anchors stable between runs.

    threshold 0.7 multiset-Jaccard ≈ "shares ~50 of 60 maindeck cards"; the
    right value is an open question (DESIGN.md §13) — it is a parameter, not
    a law.
    """
    clusters = []
    for key in list_keys:
        counts = collections.Counter(key)
        best, best_sim = None, 0.0
        for cluster in clusters:
            sim = multiset_jaccard(counts, cluster["anchor"])
            if sim > best_sim:
                best, best_sim = cluster, sim
        if best is not None and best_sim >= threshold:
            best["members"].append(key)
        else:
            clusters.append({"anchor": counts, "members": [key]})
    return clusters


def segment_bounds(boundaries):
    """Half-open [lo, hi) ISO-date segments between sorted change-point dates.
    Lexicographic compare works on ISO dates; sentinels bracket everything."""
    edges = ["0000"] + list(boundaries) + ["9999"]
    for lo, hi in zip(edges, edges[1:]):
        if lo == "0000" and hi == "9999":
            label = "all dates"
        elif lo == "0000":
            label = f"pre-{hi}"
        elif hi == "9999":
            label = f"from {lo}"
        else:
            label = f"{lo} to {hi}"
        yield lo, hi, label


def group_by(items, key):
    grouped = collections.defaultdict(list)
    for item in items:
        grouped[key(item)].append(item)
    return grouped