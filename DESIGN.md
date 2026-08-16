# DESIGN.md — the coach

Status: **phases 0–1 built** (2026-08-16): `coachlib.py`, `deck_health.py`,
`event_formats.json`, `regime_changes.json`, per-format legality caching in
`resolve_cards.py`, and `synthetic_demo.py` reproducing §6's acceptance test.
Phases 2+ remain design. This captures the architecture worked out for an AI
*coaching* layer on top of the existing local log pipeline, so the reasoning
survives while we build it.

Ground truth found en route: the Izzet-killing ban is real and dated
**2026-08-10** — Badgermole Cub, **Gran-Gran** (arena id 97328), and
Stormchaser's Talent banned in Standard, explicitly targeting Izzet/Jeskai
Lessons. Gran-Gran remains legal in Timeless/Historic/Alchemy/Brawl, exactly
the §5 cross-format story.

This is a design record, not a tutorial. Where a decision has a non-obvious
*why*, the why is written down — that's the whole point of the file. A glossary
of the concepts it leans on is at the bottom.

---

## 1. Goal, and the objective we actually optimize

The human goal: **reach Mythic in Standard, at least once.**

The naive objective is "maximize winrate." That is **wrong**, and getting the
objective wrong is the most expensive mistake available, because everything
downstream optimizes toward it. The real objective is:

> **maximize expected rank gain per unit *time*, before the monthly season reset.**

Consequences that "maximize winrate" never surfaces:

- A **familiar 55% deck can beat an unfamiliar 58% deck**, because rank climbed
  = games played × win%, and a deck you pilot fast with no punts yields more
  *rank per hour*. Game **speed, consistency, and your own familiarity** are
  first-class terms in the objective.
- **Explore/exploit is time-varying.** Early season → explore (test decks,
  tolerate variance). Late season under a deadline → exploit the deck you know
  is good and grind reps. The coach's advice literally flips based on
  days-left-in-season.
- Your **per-deck learning curve** matters: a new deck's first ~10 games
  *underestimate* its ceiling. "Abandon early" and "durdle in comfort forever"
  are both traps.

So winrate is an **input**, not the goal. It feeds a top objective layer.

## 2. Scope (v1)

**Constructed only, Standard-focused.** Limited/draft is explicitly **out**: it
is a different brain (no persistent deck identity, no bans, "good" =
card-quality-within-a-set + synergy, à la 17Lands), and it does not share the
architecture below. Format nonetheless stays a first-class dimension even in a
Standard-focused v1, because "where is my dead-in-Standard deck still alive?"
(e.g. Timeless) is a question we want answered.

## 3. Core principle

Magic is a **non-stationary** environment (see glossary): unlike poker, the
definition of "good" drifts as sets release, cards are banned, and formats
rotate. The architecture's spine is one rule:

> **Separate code by rate-of-change. Invariants live in *code*; everything
> volatile is pushed to the edges as *dated, versioned data*. Never bake a fact
> that expires into logic.**

A ban then becomes a *data update*, not a code change.

## 4. Architecture

```
╔═ OBJECTIVE LAYER ═ north star: rank gain / time, before season reset ═╗
║  • season clock → explore early / exploit late                        ║
║  • deck recommender = f(winrate CI, YOUR mastery curve, game speed,    ║
║                          consistency, rank state)                      ║
╚═══════════════════════════════════════════════════════════════════════╝
        │ pilots ▼
┌─ BRAIN ────────────────────────────────────────────────────────────────┐
│ ① Fundamentals   (code · STATIONARY)  card adv · tempo · mana · combat  │
│ ② Format context (DATED data · VOLATILE)                                │
│      legality/(card,format) ← Scryfall · format calendar · rebalances · │
│      event→format map · YOUR results                                    │
│ ③ Change-point detector  per-format regime boundaries → segment stats   │
│ ④ LLM reasoner  (RAG-not-memory) reasons over ①+② for one spot          │
└─────────────────────────────────────────────────────────────────────────┘
```

**New components (these are NOT dimensions — they are new machinery):**

- **A. Archetype clustering** — fuzzy deck identity, so winrate samples
  accumulate instead of shattering on every list edit (§5).
- **B. Process evaluator + variance filter** — coach *decisions*, not
  outcomes; stay silent on variance losses.
- **C. Opponent archetype classifier** over partial reveals — confidence-stamped.
- **D. Statistical humility** — Wilson intervals; trust *dated* events fast,
  noisy trends slowly.
- **E. Rank-trajectory reconstruction** (time-join of `rank` deltas with games)
  + game hygiene (drop concedes / disconnects).

## 5. The grain and the dimensions

The single most important data-modeling decision: the **grain** (finest thing
one row of results describes) is

```
(archetype, format, match_type, on_play, time-segment, rank_tier) → win/loss
```

and legality's grain is `(card, format) → status, effective_date`.

Why each axis exists:

- **archetype**, not exact list. Exact-multiset identity (what the Forge
  exporter uses) is *wrong for the coach*: constant tinkering fragments winrate
  into 3-game samples that never reach significance. Hence component **A**, and
  a grain hierarchy: `card → list (exact 75) → archetype (fuzzy) → colors`.
  Legality lives at *card*, your registration at *list*, winrate + coaching +
  metagame at *archetype*.
- **format** — a card is never "banned," it is banned *in a format*. Averaging
  across formats is **Simpson's paradox** (see glossary): it hid that Izzet
  Thunder is dead in Standard (38%) yet thriving in Timeless (64%).
- **match_type** (Bo1 vs Bo3) — Bo1 uses a hand-smoother that biases opening-hand
  land counts, so mulligan stats must be read per match_type. Bo3 also mutates
  the deck mid-match via sideboarding (§ risk of two grains: registered-75 vs
  in-game-60).
- **on_play** — known confounder, already logged.
- **time-segment** — bounded by change-points (§6).
- **rank_tier** — a confounder that must be carried, not averaged away (climbing
  raises opponent strength, so improving play can look like flat winrate).

## 6. Regime changes (the non-stationarity, made concrete)

Two kinds of concept drift, handled differently:

- **Sudden regime change** — bans, set releases, rotation, Alchemy rebalances.
  These are *dated*. They are change-points: segment/decay stats **across** them,
  because pre-event games are a different world. A ban is decisive with **zero**
  new games.
- **Gradual drift** — metagame evolution within a fixed pool. Must be *inferred*
  from noisy data → statistical care (component D).

Regime sources differ **per format** — another reason format is first-class:

| Format   | Rotates? | Change-point sources                  |
|----------|----------|---------------------------------------|
| Standard | yes      | bans **+ rotation**                   |
| Timeless | no       | bans (rare) + restricted (1-of) list  |
| Historic | no       | bans + suspensions                    |
| Alchemy  | no       | bans **+ rebalances** (new `A-` grpId)|

The Izzet Thunder test case, which Phase 1 must reproduce automatically:

> ⚠️ Regime change <date> — `<card>` (4 copies of "Izzet Thunder") banned in
> Standard. Winrate before 64% (42g) / after 38% (11g). Engine gone.
> *(Confidence: low — 11 post-ban games. Play ~15 more or pivot.)*
> Still legal & strong in **Timeless** (64%, 30g) → queue Timeless to keep
> laddering it, or replace the tempo role for Standard.

## 7. Cross-cutting laws

1. **Volatility → dated data at the edges.** Even the `event→format` lookup is
   volatile data (Arena adds queues) and carries a date; it is *not* hardcoded.
2. **Stamp every statistic with the population it describes** — "your Diamond
   Bo1 field, last 14 days," never a naked "the meta is." No unbiased ground
   truth exists in this system; every number is an estimator with a bias.
3. **Coach process, not outcome.** Grade observable *decisions* (missed land
   drop, kept a 1-lander on the draw, tapped into open mana), not results. One
   `won` bit is trying to constrain three unknowns at once — deck quality × play
   quality × variance — so it is a terrible label. This is poker's anti-*resulting*
   lesson.
4. **Detect and stay silent on variance losses** (mana screw/flood). Over-coaching
   noise destroys trust.

## 8. The two traps the design exists to avoid

- **Trap 1 — trusting a fixed-cutoff model for volatile facts.** The LLM
  (component ④) has a training cutoff and will confidently give *stale*
  post-ban metagame takes. Rule: **RAG, not memory.** For anything volatile,
  retrieve the current fact (Scryfall legality, your recent winrates, a dated
  meta snapshot) and inject it; the LLM *reasons over facts you supply*, it is
  never the source of them.
- **Trap 2 — Simpson's paradox.** Under-dimensioning results (§5) yields
  confident, wrong aggregates.

## 9. Data feeds and their grains

- **Scryfall** (already integrated in `resolve_cards.py`): extend the cache to
  store per-format `legalities` and `released_at`/`set`. Grain `(card, format)`.
  Gives ban/rotation/rebalance detection almost for free.
- **Your `game_result` logs** (already parsed): dated, per-deck outcomes. The
  empirical, *personal*, self-correcting signal. Ground truth for v1 is **your
  own results only**; an external metagame feed (17Lands/Goldfish-style, dated,
  expiring) is designed-for but deferred.
- **`rank` events**: reconstruct the rank trajectory by time-joining deltas with
  games (component E) to attribute pip gain to games.

## 10. The data-availability risk (and the venue split it forces)

`game_result` contains opening hand, draws, mulligans, turn count, and revealed
opponent cards — enough to coach **mulligan / keep decisions**, but it is **not
a play-by-play.** It cannot see "attacked into open mana" or "tapped out into a
counter." Full **process coaching (component B) is data-blocked** from the
current pipeline; that detail lives in the GRE game-state stream inside
`Player.log`, which the upstream 17Lands parser does not surface as `game_result`.

This closes the loop back to the original "Forge sidecar" idea. The two halves
of the brain are fed by the source that can actually see what they need:

> **Forge = the venue for *process* coaching** (full live board state → B).
> **Real Arena logs = the venue for the *deck-health + objective* layers**
> (real ladder outcomes → objective layer + ②/③).

Also: live coaching on **real ranked Arena games is outside-assistance and
likely against Arena's ToS.** Real-Arena coaching must be strictly **post-game.**
Live coaching belongs in the Forge sandbox, where it is just practice.

## 11. Phased build (dependency-ordered, riskiest-data-first)

- **Phase 0 · Data foundations.** Extend `resolve_cards.py` for per-format
  `legalities` + `released_at`; build the dated `event→format` table;
  game-hygiene filter + rank-trajectory reconstruction. *Unblocks everything.*
- **Phase 1 · Deck-health spine** (no LLM, no Forge). Archetype clustering
  **[A]** → per-`(archetype,format,segment)` winrate with Wilson intervals
  **[D]** → change-point detection at ban/rotation dates **[③]**. *Reproduces
  the Izzet Thunder flag.*
- **Phase 2 · Objective layer.** Season clock + explore/exploit + rank-per-time
  deck recommender.
- **Phase 3 · Coaching brain.** Process evaluator + variance filter **[B]**
  (mind the §10 data limit), LLM reasoner **[④]**, opponent classifier **[C]**.
- **Phase 4 · (optional) Forge live sandbox.** The observation spike: cheapest
  is tailing a text game-log (low fidelity, low coupling); destination is an
  in-JVM observer on Forge's event bus (high fidelity, high maintenance). Only
  if live practice proves worth it.

## 12. Key decisions and their rationale

- **Objective = rank/time before reset, not winrate.** Winrate ignores speed,
  familiarity, consistency, and the season clock — all of which move real rank.
- **Constructed-only v1.** Limited needs a different brain; the goal is Standard.
- **Format is a dimension on everything.** Legality and "good" are per-format.
- **Archetype (fuzzy) identity, not exact list.** Exact identity shatters
  samples; the coach needs stats to accumulate. *New component, not a dimension.*
- **Coach process, not outcome.** Outcome is a 3-unknowns-in-1-bit label.
- **RAG, not memory, for volatile facts.** Avoids stale-cutoff hallucinations.
- **Own results as ground truth for v1; external meta deferred but designed-for.**

## 13. Open questions / deferred

- Archetype-similarity threshold ("how similar is the same deck?").
- Bo3 sideboarding: model registered-75 vs in-game-60 as two grains.
- Opponent inference from **censored** reveals (absence ≠ not in deck).
- External metagame feed (source, freshness, bias, ToS).
- Statistical method for *gradual* drift vs the easy dated change-points.
- Whether Phase 3 process coaching justifies the Forge integration cost, or a
  GRE-stream parser on `Player.log` is the lighter path.

## 14. Glossary

- **Non-stationary / concept drift** — the target ("what's good") changes over
  time. Poker is stationary; Magic is not.
- **Regime change / change-point** — a discrete, dated shift (ban, rotation,
  set, rebalance) that makes older data a different world.
- **Grain** — the finest thing one row of a data table describes. Choosing it is
  the core data-modeling decision.
- **Simpson's paradox** — an aggregate that reverses/erases the truth hiding in
  subgroups (mixing Standard + Timeless winrate).
- **Censoring / missing-not-at-random** — you observe only part of the data
  (opponent's *revealed* cards); absence is not evidence of absence.
- **Explore/exploit** — the tradeoff between trying new options and grinding a
  known-good one; here it is *time-varying* against the season clock.
- **Credit assignment** — attributing an outcome to the decision that caused it;
  hard because one game's result is noisy.
- **Resulting** — poker's fallacy of judging a decision by its outcome rather
  than its quality.
- **Wilson interval** — a confidence interval for a proportion (winrate) that
  behaves well on small samples; use instead of naked percentages.
- **RAG (retrieval-augmented generation)** — inject retrieved current facts into
  the prompt so the model reasons over them instead of its stale parameters.
- **Spike** — a small throwaway experiment to de-risk the single most uncertain
  part before committing to a design.