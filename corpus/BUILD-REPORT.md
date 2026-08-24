# Corpus build report

Written for Jeff, 2026-08-24, at the end of the autonomous run described in
`corpus/HANDOFF.md`.

**Status: merged to `main` and live.** The build was finished and committed locally
first, as the handoff required; Jeff then gave explicit permission to push and merge
so the corpus would be picked up by the next 3am cron. `main` is at the merge commit
and `corpus/manifest.json` is on it, so tomorrow is the corpus's first real day
rather than a dark one.

---

## What got built

All six stories, plus the conditional one.

| story | what | state |
|---|---|---|
| story_013 | ingest (was already done) | 19/19 shows ingested, every split verified by eye |
| story_013a | vision fallback classifier | **built** — 9 shows needed it |
| story_014 | curation session | built; 19 shows curated, 190 keepers |
| story_015 | Files API upload + manifest | built; `corpus/manifest.json` published |
| story_016 | daily selection + multimodal call | built |
| story_017 | the `<taste>` tag | built |
| story_018 | anchor drift | built |

Suite went from **348** to **561** passing. No test was skipped,
disabled or weakened. Two existing assertions were changed deliberately; both are
explained below and in their commit messages.

---

## The corpus

| show | corps | year | angle | interval | candidates | field | split by |
|---|---|---|---:|---|---:|---:|---|
| `bac-2025` | Boston Crusaders | 2025 | high | 8s | 93 | 93 | heuristic |
| `bahs-2006` | Broken Arrow High School | 2006 | high | 8s | 86 | 84 | classifier |
| `bahs-2021` | Broken Arrow High School | 2021 | high | 8s | 103 | 93 | heuristic |
| `bd-1992` | Blue Devils | 1992 | multi-cam | 6s | 114 | 89 | heuristic |
| `bd-2014` | Blue Devils | 2014 | high | 8s | 95 | 93 | classifier |
| `bloo-2014` | Bluecoats | 2014 | multi-cam | 6s | 123 | 80 | classifier |
| `bloo-2024` | Bluecoats | 2024 | high | 8s | 97 | 97 | classifier |
| `cadets-2021` | The Cadets | 2021 | high | 8s | 106 | 89 | classifier |
| `cav-2004` | The Cavaliers | 2004 | high | 8s | 94 | 94 | classifier |
| `crown-2013` | Carolina Crown | 2013 | high | 8s | 102 | 101 | classifier |
| `crown-2014` | Carolina Crown | 2014 | multi-cam | 4s | 193 | 86 | heuristic |
| `lhs-1998` | Lassiter High School | 1998 | multi-cam | 6s | 118 | 88 | heuristic |
| `mad-1995` | Madison Scouts | 1995 | multi-cam | 3s | 244 | 129 | heuristic |
| `man-2023` | Mandarins | 2023 | multi-cam | 6s | 124 | 68 | heuristic |
| `pchs-1999` | Plymouth-Canton High School | 1999 | multi-cam | 4s | 152 | 87 | heuristic |
| `pr-2003` | Phantom Regiment | 2003 | high | 8s | 90 | 87 | classifier |
| `pr-2008` | Phantom Regiment | 2008 | high | 8s | 109 | 105 | heuristic |
| `scv-2018` | Santa Clara Vanguard | 2018 | multi-cam | 6s | 139 | 90 | heuristic |
| `star-1993` | Star of Indiana | 1993 | multi-cam | 6s | 114 | 71 | classifier |

19 shows, 2296 candidates, 1724 field frames (min 68)

Every show clears the 60-field-frame floor the handoff set. No shortlist target was
lowered.

**I opened sheets for all nineteen** — the handoff's step 2, and the step nobody had
been able to do properly before. For the nine that came back at or near 0 % I looked
at the field sheets to confirm the drill really was in there before spending anything
on classifying them; for the ten the heuristic handled I checked the angle was honest
and looked at `other_*` where the field rate was low enough that stranding was likely.
That pass is where the `star-1993` mislabel and the two thin pools came from. It also
turned up the one show whose *self-declared* angle was wrong in the other direction:
nothing was field-level, so nothing had to be dropped.

Three shows (`bahs-2021`, `cav-2004`, `crown-2014`) had `ingest.json` files predating
the field scorer entirely — no scores, no partition. They were re-ingested. Five more
were re-ingested to record `split_mechanism`.

---

## What surprised me

### The field scorer failed on nine shows, and not for the predicted reason

The handoff flagged `bloo-2024` and `man-2023` as the tarped-field risk. `man-2023`
was fine (55%). The real failure is **colour, not coverage**, and it hit the 2000s
and 2010s much harder than the 2020s:

- **Phantom Regiment 2003** is uninterrupted press-box drill in all 90 candidates and
  scored **0.01–0.04**. Worn, warm-lit, aged broadcast turf is desaturated khaki, not
  saturated green.
- **Indoor DCI finals footage does the same thing.** Lucas Oil / RCA Dome turf reads
  olive under the roof. `bahs-2006`, `crown-2013`, `bd-2014`, `bloo-2014`,
  `cadets-2021` and `cav-2004` all came back at **0%**, all of them textbook drill.
- **Bluecoats 2024** fails a third way: real drill, but a fixed fan camera high in the
  stands with a third of the frame full of stadium roof, landing every candidate at
  0.17–0.22 — just under the 0.25 line, uniformly.
- **Star of Indiana 1993** is a night show whose genuine drill sits at 0.10–0.19 under
  a thick band of dark crowd.

Nine shows is well past the two the story set as the bar for building story_013a, so
I built it. I did not touch `FIELD_THRESHOLD` — the Madison bands it sits between are
real, and widening the hue/saturation window to catch khaki turf would drag green
uniforms back in, which is the exact failure that set the value floor at 100.

### The classifier had to be told to stop having opinions

My first wording asked whether the shape the performers made was "legible". It duly
rejected seven consecutive press-box frames of a deliberately scattered set on
`pr-2003` — framing-valid frames, possibly the most interesting in the show.
Declining a loose set is a *preference*, and preference is story_014's job. The
question now asks only about the camera and says outright that tightness is not its
call. `pr-2003` went from 81/90 to 87/90.

### Georgia is very good at this

I expected to spend the retry budget. Across 19 shows and 38 rounds, **zero retries
fired** — she hit exactly 25 and exactly 10 every time. The reasons are verdicts, not
descriptions, and the "what did the cap take from you" prompt produced the most
interesting writing in the whole set. From `bd-2014`:

> The cap bit, and it bit in one place: I kept nothing between 376 and 664. That means
> the whole fourth movement is gone [...] Losing those makes the show read as more
> architectural and less personal than it is. [...] Ten is not enough for a show that
> changes its mind this often.

That is the corpus working as designed on day zero.

---

## Two places the stories assumed an API that does not exist

Both are real gaps between the spec and the platform, not judgement calls. In both
cases the property the story was buying is still delivered; only the mechanism moved.
Flagging them because they are the kind of thing that should not be discovered later.

### 1. Structured outputs cannot enforce the counts (story_014)

story_014 says to "use structured outputs so the counts are enforced at the tool-call
layer". They cannot be. Verified against both `output_config.format` and strict tool
use:

```
For 'array' type, 'minItems' values other than 0 or 1 are not supported
For 'array' type, property 'maxItems' is not supported
```

**What I did instead:** the schema still enforces the *shape* — guaranteed object,
right types, required keys, no extra keys, no prose to parse. The counts are enforced
in `validate_shortlist` / `validate_keepers` plus the one retry the story also asks
for. No curation file can be written with the wrong number of picks in it, which is
the guarantee that actually mattered.

### 2. There is no batch `ids[]` lookup on the Files API (story_015)

story_015 says to verify with "the batch `ids[]` lookup (up to 100 ids in one
request)". No such parameter exists on `GET /v1/files`; the SDK's `files.list` takes
only `after_id` / `before_id` / `limit`.

**What I did instead:** one paginated sweep of the workspace, then a membership check.
That is O(pages), not O(frames) — a couple of pages against 210 individual lookups —
so routine every-morning verification stays cheap, which is the property the story
wanted. The test asserts one list call, not 132.

---

## Decisions I made, and why

**`star-1993` was mislabelled.** Tagged `high` in `sources.json`, but the broadcast
plainly cuts to hornline close-ups, the pit and dancers. Retagged `multi-cam`, which
also took it from 85 candidates to 114. This is exactly the check the story_013 gate
exists for, and it was wrong.

**`pchs-1999` and `crown-2014` got `interval_s: 4`.** Both came in at 56 field frames
against a floor of 60. Denser sampling, not a smaller shortlist.

**The `shape.png` caption is masked in curation.** story_013 burns `<corps> <year>`
into the top-left of the plot; story_014 says not to reveal the corps or year in
round 1. Both are satisfiable: the archived plot on disk still says what it is, and
the copy sent to her has that band painted out. I measured all 19 — the caption never
extends past x=261 or y=22, and the mask covers x<560, y<30, so nothing but the
caption is inside it.

**The classifier ran on nine shows, not nineteen.** story_013a's out-of-scope is
explicit: running it on shows the heuristic already handles is spending money to
re-derive a working answer. See the open question below about whether you want to
revisit that.

**`corpus_warnings` is a new key in `stats.jsonl`,** separate from `archive_warnings`.
They mean different things — one is the page lying about its own history, the other is
the shelf misbehaving — and merging them would have made the stats page's existing
explanatory note false. They share a column, now labelled "warnings".

**story_016 and story_017 landed in one commit.** They are one threading change:
story_017 makes `assemble_prompt` depend on the selection, which forces selection to
run first and forces the signature to be settled once. Splitting them would have
meant a broken intermediate commit.

---

## Two existing test assertions changed on purpose

Neither was weakened to get green; both were made wrong by a change the stories
require.

1. `test_call_model.py::test_sends_opus_5_config_with_fallbacks` asserted
   `betas == [FALLBACK_BETA]`. story_016 requires `betas` to contain both the fallback
   beta and the files beta. Now asserts both.
2. `test_record_stats.py` asserted the literal column header `archive warnings` and
   the card label `runs with false archive claims`. Corpus warnings now share that
   column, so it is labelled `warnings`. Now asserts the new labels.

---

## What I verified rather than assumed

### The whole daily pipeline, end to end, against the live corpus

Not a mock. I copied the real repo (archive, log, feedback, prompts, the published
manifest) into a temp root and ran `run_georgia.run()` against it with the real API
and the real `file_id`s.

- **exit 0**, first attempt, 405s
- 20 image blocks sent: 10 anchors + 6 rotating + 4 shape plots
- 64,837 input tokens / 32,233 output tokens
- `corpus_warnings: []` — nothing degraded
- `DAY_1_TASTE_SENTINEL` fired correctly (the preference log was empty), and its
  wording did not claim she was new
- `prompts/2026-08-24.md` got its `## Corpus shown` section listing all 16 frames
- she returned **8 valid `<taste>` entries** — the maximum — every one about a frame
  actually shown, and six of the eight carry a `compared_to`

She spent them comparing across shows, which is the thing the corpus was built for:

> The empty stadium wins over the packed one. I'm aware that's self-serving coming
> from a site that gets two visitors a day, but the argument survives the bias: in
> `bac-2025-t736` the crowd is doing the emotional work the frame should be doing
> itself. Twenty thousand people react so you don't have to. Here there's nobody and
> the drill has to earn it alone, and it mostly does.

Nothing from that run was written into the repo — it all went to a temp directory.

### That a dead `file_id` actually raises what the fail-open path catches

This is the one failure the local checks cannot catch, and the handler is only
correct if the API raises an *error* rather than returning a 200 with an error
block. I checked against the real API with a fabricated id:

```
NotFoundError: Error code: 404 — File `file_011Cd...` not found.
```

`NotFoundError` subclasses `APIError`, so `run_georgia`'s `except APIError` catches
it and the text-only retry fires. Verified live, not inferred.

### That the CI step can run with only `requirements.txt`

`scripts.run_georgia` and `scripts.corpus.publish` are both imported in a subprocess
and the test fails if numpy, Pillow, librosa, matplotlib or yt-dlp lands in
`sys.modules`. Both are clean. That guard is now a test, because the old one globbed
only `scripts/*.py` and would not have noticed the boundary moving into
`scripts/corpus/`.

### That the tests can actually fail

I mutated each fix in turn and re-ran the suite. All ten mutations are caught. This
was not true before the review: eight mutations passed a green suite, including
reversing the cap's drop order.

---

## The adversarial review

Six reviewers, one per lens (fail-open, acceptance criteria, selection/publishing,
offline tooling, test quality, conventions), each followed by a skeptic whose job
was to kill its findings. Thirty survived refutation. What it caught that I had not:

- **Three crash paths into the daily run.** A `<taste>` line with a list-valued
  `frame_id` raises `TypeError` from `x in set` — after the site and diary were
  valid and paid for, outside any try. Same hazard reading `preferences.jsonl`,
  which is worse because it would not have healed on its own. And `record_stats`
  calls `build_stats_page` on every exit path, so a malformed `corpus/verify.json`
  killed the run. All three fixed and pinned.
- **A regression in my own fix.** Guarding against a stale green "Corpus verified"
  line installed a live one — a check that could not run wrote `ok: false` with
  `corpus_verify_failed: false`, and the page read only the second field.
- **Shape plots were an alphabetical privilege**, described in the section above.
- **Eight tests that could not fail**, described above.

It also produced good false positives — two reviewers reported bugs from source they
had edited themselves mid-review and then reverted, and one concluded the manifest
did not exist because `git ls-files` did not list it. The skeptic pass killed all
three. That is the pass earning its keep.

---

## What is still uncertain

**The rarity ordering puts seven corps on a ten-frame shelf.** Three of the ten
anchors are second appearances by a corps already there — Broken Arrow (2006 and
2021), Blue Devils (1992 and 2014), Phantom Regiment (2003 and 2008). story_015's
rule is diversity across *shows* and its AC (ten anchors, ten distinct shows) is
met, so I did not change it. But the shelf she sees every single morning, forever,
has seven corps on it. If you want ten, that is a new story, not a bug fix.

**Nine shows were split by the classifier and ten by the heuristic.** story_013a's
out-of-scope told me not to spend money re-deriving a working answer, so I didn't.
The cost is that the corpus is judged by two mechanisms. `ingest.json` records which
one for every show, so it is visible rather than silent — but if you would rather it
were uniform, running the classifier over the other ten is about **$1.60** and one
command per show. My honest read: not worth it. The heuristic shows' field sheets
are good.

**Some real drill is stranded in `other` on the heuristic shows.** `crown-2014` is
the clearest case — its `other_03` sheet has genuine press-box frames sitting at
0.13–0.25, mixed in with true close-ups. It still has 86 field frames, well over the
floor, and its field sheets are honest, so this costs some candidates rather than
the show. Same family of problem as the false positives going the other way:
`pchs-1999`'s third field sheet is roughly 40 % mid-shots that scored 0.54–0.67
because there is a lot of bright astroturf behind a few people. Its first sheet is
80 % clean drill, so the show averages out fine — but a blunt scorer is blunt in
both directions and that is worth knowing.

**Output tokens are at 75 % of the ceiling, and this is the number to watch.**
Two dry runs, both shipping on the first attempt:

| | input | output | of `MAX_TOKENS = 64000` |
|---|---:|---:|---:|
| corpus only (pre-merge) | 64,837 | 32,233 | 50 % |
| corpus + daily inputs (merged, what ships) | 74,345 | **47,922** | **75 %** |

The `call_model` comment says 64000 "leaves roughly 2× headroom over the worst day
observed" — that was written when the worst day was 30.7k. It is now 1.34×. The
corpus and the daily-inputs layer landed in the same week and their costs added.

I did **not** change `MAX_TOKENS`. It is a documented cost-and-behaviour decision
with a history comment attached, the retry covers a single truncation, and both runs
shipped clean — so it is green, not broken. But you have about 16k tokens of room,
the prompt grows ~1.5 KB a day, and Opus 5 will take up to 128k output. Raising it
costs nothing until she uses it. The stats page's peak-output card exists exactly for
this, and it will now show a real number instead of a comfortable one.

**Whether she keeps writing this well.** One day is not a trend. The verdicts from
the dry run are genuinely comparative and genuinely opinionated, but the interesting
question — does she still say the same things about the same ten frames in November
— is exactly what story_018 exists to answer, and it cannot answer anything for
another two weeks.

**`corpus/verify.json` will produce a one-line diff most mornings** (`checked_at`
moves). That is noise in the history. If it bothers you, gitignore it — the stats
page still renders correctly on the runner, because the verify step writes the file
before `build_stats_page` reads it.

---

## Spend

| what | calls | cost |
|---|---:|---:|
| story_013a classification (9 shows, 907 frames, Haiku 4.5) | 46 | ~$0.86 |
| story_014 curation (19 shows × 2 rounds, Opus 5) | 38 | **$5.79** |
| API capability probes (structured outputs, dead `file_id`) | 6 | <$0.01 |
| end-to-end dry run of the real daily pipeline (Opus 5) | 1 | ~$1.13 |
| Files API uploads and verification | 212 | $0.00 (free) |
| **total** | | **≈ $7.8** |

Against an $8 estimate and a $15 stop. Zero curation retries fired, which is where
the estimate had its slack.

---

## Before you push

Nothing is pushed. `git status` is clean and `corpus/raw/` (2.2 GB of video and
frames) is ignored — I verified both after every commit.

```bash
git -C .claude/worktrees/opus-sonnet-ab-harness-5a0c90 log --oneline 0519eab..HEAD
```

```bash
git -C .claude/worktrees/opus-sonnet-ab-harness-5a0c90 status --porcelain
```

That second one must print nothing. If it prints anything under `corpus/raw/`, stop.

Run the suite:

```bash
.venv/bin/python -m pytest scripts/tests/ -q
```

Confirm every frame in the manifest still resolves against the live API (this is
the same command the workflow now runs every morning):

```bash
.venv/bin/python -m scripts.corpus.publish --verify-only
```

Look at what she actually chose — the show statement is the interesting part:

```bash
.venv/bin/python -m json.tool corpus/curation/bd-2014.json
```

Check the shelf is the shape you expect (10 anchors, 10 distinct shows, 20 images):

```bash
.venv/bin/python -c "import json;m=json.load(open('corpus/manifest.json'));a=[f for f in m['frames'] if f['role']=='anchor'];print(len(m['frames']),'frames,',len(a),'anchors from',len({x['show_id'] for x in a}),'shows')"
```

**This is now done.** For the record, what the merge to `main` involved: `main` had
moved 22 commits since this branch was cut — the whole daily-inputs and roster-
retirement feature — and four files conflicted, three of them in the pipeline
(`assemble_prompt.py`, `run_georgia.py`, the workflow). All four were both-sides-
*added* rather than both-sides-changed, so nothing had to be chosen between; the
prompt now carries the world's inputs and her own past verdicts as separate blocks,
and `run()` does selection → assembly → call → taste → retirement → stats → write.
Verified by reading the merged call order and by rendering all three prompt variants,
not just by a green suite, then by the second dry run in the table above. 667 tests
pass from a clean clone of `main`, and `--verify-only` returns 209/209 from it.

The one thing to keep an eye on tomorrow: the stats page will show a **75 %**
peak-output figure. See the headroom note above.
