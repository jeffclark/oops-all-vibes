# oops-all-vibes — Engineering Stories
_Generated 2026-04-22. Source: `prd.md` (rev 3)._

Target implementer: **Claude Code**. Each story is self-contained and independently testable. Dependencies between stories are explicit.

---

## Project Facts (shared context for every story)

- **Repo**: `jeffclark/oops-all-vibes` (public, GitHub)
- **Local working folder**: `oops-all-vibes/` under Cowork OS root
- **Hosting**: GitHub Pages (repo's `main` branch)
- **Language**: Python 3.11+ for the pipeline
- **AI model**: Claude Sonnet 4.6 (`claude-sonnet-4-6`) via official Anthropic Python SDK
- **Secrets / env**:
  - `ANTHROPIC_API_KEY` — GitHub Actions secret; locally via env var
  - `GOATCOUNTER_API_KEY` — GitHub Actions secret; used by the feedback fetcher
  - `GOATCOUNTER_CODE` — GitHub Actions variable (non-secret; site codes are public)
- **Cron time**: `0 7 * * *` UTC in GitHub Actions (≈3am EST / 2am EDT — acceptable drift)
- **Dry run first**: all stories target dry-run readiness on `jeffclark.github.io/oops-all-vibes`. DNS cutover to `clarkle.com` is the final manual step (story_012).
- **Inviolable facts** (must appear in every generated site): Jeff Clark's name, `jeff@clarkle.com`, his LinkedIn URL, his LinkedIn title, the 10 project titles.
- **Pipeline-injected post-hoc** (not Georgia's responsibility): the GoatCounter tracking script, a footer link to today's prompt file.
- **Georgia's creative freedom**: design, copy, tone, voice, layout, structure, medium, everything else.

---

## Daily Pipeline Order

The GitHub Action runs these scripts in sequence. Each step can fail gracefully without crashing the next meaningful step.

```
1. scripts/fetch_feedback.py   → writes feedback/<yesterday>.json (skips cleanly on day 1 or API failure)
2. scripts/run_georgia.py      → assemble prompt → call Sonnet → validate → retry once if needed → write outputs → commit
                                 → appends one line to stats.jsonl
                                 → regenerates stats.html
```

Failure modes:
- Fetcher fails → no feedback file written → assembly uses the no-feedback sentinel → Georgia builds blind
- Sonnet API error → run_georgia.py exits 1, no commit, yesterday's site stays up
- Validation fails twice → same as API error

---

## Feedback File Schema

Every day (after day 1), `scripts/fetch_feedback.py` writes `feedback/<yesterday>.json` with this structure:

```json
{
  "date": "2026-04-21",
  "yesterday": {
    "visitors": 142,
    "pageviews": 289
  },
  "recent": {
    "last_7_days_visitors": 487,
    "last_7_days_avg": 69.6,
    "last_30_days_visitors": 1204,
    "last_30_days_avg": 40.1
  },
  "historical": {
    "all_time_visitors": 8430,
    "days_live": 68,
    "peak_day": { "date": "2026-03-15", "visitors": 512 }
  },
  "trend": {
    "yesterday_vs_7d_avg": 2.04,
    "week_over_week_pct": 32.0
  },
  "jeff_note": null
}
```

- `date` is the date the stats describe (yesterday relative to today's run).
- `jeff_note` stays `null` until a Jeff-note mechanism is built (out of scope for v1).
- All numeric fields are `null` if GoatCounter has no data for that period (day-1 era).

---

## Story Dependency Graph

```
001 (scaffold) ──► 002 (seed content) ──► 003 (prompt assembly) ──► 004 (sonnet call)
                                                                         │
                                                                         ▼
                                                  005 (validation) ──► 006 (run orchestration)
                                                                         │
                                        ┌────────────────────────────────┼──────────────┐
                                        ▼                                ▼              ▼
                                  007 (commit pipeline)           009 (feedback      011 (observability)
                                        │                              fetcher)
                                        ▼                                │
                                 008 (GH Actions cron) ◄─────────────────┘
                                        │
                            010 (client-side injection)
                                        │
                                        ▼
                              012 (dry run + DNS cutover)
```

---

## story_001 — Scaffold repo and folder structure

**Goal**: Create the working folder and the public GitHub repo with empty directory structure ready for the pipeline.

**Depends on**: none

**Files to create**:
- `oops-all-vibes/` folder (already exists — verify)
- `oops-all-vibes/.gitignore` — ignore `.env`, `__pycache__/`, `*.pyc`, `.venv/`, `.DS_Store`
- `oops-all-vibes/README.md` — one-paragraph stub: what this repo is, link to Jeff's LinkedIn, note that Georgia runs it. Can be stubbed; Georgia may rewrite later.
- `oops-all-vibes/archive/.gitkeep`
- `oops-all-vibes/log/.gitkeep`
- `oops-all-vibes/feedback/.gitkeep`
- `oops-all-vibes/prompts/.gitkeep`
- `oops-all-vibes/scripts/.gitkeep`
- `oops-all-vibes/.github/workflows/.gitkeep`

**Implementation notes**:
- Initialize git in `oops-all-vibes/`
- Create the GitHub repo `jeffclark/oops-all-vibes` (public) using `gh repo create` — confirm with Jeff before pushing if `gh` auth isn't already present
- Push initial commit to `main`
- Do NOT yet enable GitHub Pages — that happens in story_012

**Acceptance criteria**:
- [ ] `oops-all-vibes/` contains all directories listed above
- [ ] `git status` in the folder is clean after initial commit
- [ ] `gh repo view jeffclark/oops-all-vibes` returns a public repo
- [ ] `main` branch tracks `origin/main`

**Out of scope**:
- Any pipeline code
- GitHub Pages enablement
- Any workflow YAML

---

## story_002 — Seed content: facts.json and Georgia's soul doc

**Goal**: Populate the two content files Georgia reads as Layer 1 (Soul) and Layer 2 (Facts).

**Depends on**: story_001

**Files to create/modify**:
- `oops-all-vibes/georgia-soul.md` — move the existing draft from Cowork OS root (`/Cowork OS/georgia-soul.md`) into the project folder. Then append a new `## Guardrails` section (below).
- `oops-all-vibes/facts.json` — new file with the structure below.

**`georgia-soul.md` additions (append to existing doc)**:

```markdown
## Guardrails

These are the lines I do not cross, regardless of theme or mood:

- No commentary on Jeff's current or past employers by name.
- No political takes. I have opinions about many things; that is not one of them.
- No impersonation of named real people other than Jeff.
- No sustained negativity in the diary. If a day was bad, I say so and move on. I don't stew.
- Weird, never cruel. The chaos is playful. If a reader feels diminished, I overcorrected.
```

**`facts.json` structure**:

```json
{
  "name": "Jeff Clark",
  "email": "jeff@clarkle.com",
  "linkedin_url": "https://www.linkedin.com/in/serialcreative",
  "linkedin_title": "Director of Product at LeagueApps",
  "projects": [
    {
      "title": "Autoscope",
      "description": "A Slack-based agent that watches product discussions and produces structured scoping docs. When a team mentions a feature idea, Autoscope extracts context from the last 72 hours of thread history, drafts a one-page brief with goal, non-goals, and open questions, and posts it back for review. Built on Anthropic's tool-use API.",
      "link": "https://example.com/autoscope",
      "image": "https://picsum.photos/seed/autoscope/400/300"
    },
    {
      "title": "Currents",
      "description": "A personal knowledge tool that ingests your recent reads — articles, papers, tweets, podcast transcripts — and surfaces the thread connecting them. Instead of static 'saved for later' piles, Currents asks what you're trying to understand and returns a synthesis across sources.",
      "link": "https://example.com/currents",
      "image": "https://picsum.photos/seed/currents/400/300"
    },
    {
      "title": "Deputy",
      "description": "An AI pair for one-on-one meetings. Deputy joins the call, listens, and two hours later delivers a draft follow-up with decisions, action items, and three questions you didn't ask but should have. Built for managers who run more than ten one-on-ones a week.",
      "link": "https://example.com/deputy",
      "image": "https://picsum.photos/seed/deputy/400/300"
    },
    {
      "title": "Field Guide",
      "description": "A browser extension that turns any web page into a learning artifact. Highlight a concept you don't fully grasp, and Field Guide generates a two-minute explainer scoped to what you already know, with source links for deeper reading.",
      "link": "https://example.com/fieldguide",
      "image": "https://picsum.photos/seed/fieldguide/400/300"
    },
    {
      "title": "Metric Anomaly Bot",
      "description": "A nightly agent that watches your product analytics, flags statistically unusual movements, and proposes three possible causes ranked by plausibility. Cuts the time from 'metric looks weird' to 'metric root-caused' from hours to minutes.",
      "link": "https://example.com/metric-anomaly",
      "image": "https://picsum.photos/seed/metric-anomaly/400/300"
    },
    {
      "title": "Pitch Critic",
      "description": "Upload a pitch deck; get brutally honest feedback from three AI reviewers each modeled on a different investor archetype — skeptic, pattern-matcher, domain expert. Tested against real deck outcomes to calibrate bluntness.",
      "link": "https://example.com/pitch-critic",
      "image": "https://picsum.photos/seed/pitch-critic/400/300"
    },
    {
      "title": "Scribe",
      "description": "A terminal-native coding assistant that writes commit messages by reading the actual diff rather than generic templates. Knows when a commit is a bugfix, a feature, a refactor, or cleanup, and adjusts tone accordingly.",
      "link": "https://example.com/scribe",
      "image": "https://picsum.photos/seed/scribe/400/300"
    },
    {
      "title": "Salience",
      "description": "An email triage tool that reads your inbox and sorts it by 'what actually matters to you this week,' not by sender or date. Learns from which emails you open and respond to versus which ones you skim and archive.",
      "link": "https://example.com/salience",
      "image": "https://picsum.photos/seed/salience/400/300"
    },
    {
      "title": "Tributary",
      "description": "A weekly digest tool for founders and product leaders. Reads your calendar, your last week's Slack activity, and your Linear tickets, and produces a narrative summary of what the week was actually about. Useful for post-hoc clarity and board updates.",
      "link": "https://example.com/tributary",
      "image": "https://picsum.photos/seed/tributary/400/300"
    },
    {
      "title": "Witness",
      "description": "An audio companion for long walks. You talk out loud about a problem; Witness listens, asks occasional clarifying questions, and at the end gives you a structured transcript of what you figured out. Built for people who think best by talking.",
      "link": "https://example.com/witness",
      "image": "https://picsum.photos/seed/witness/400/300"
    }
  ]
}
```

Use these 10 placeholder projects verbatim for the dry run. They are plausible-sounding but fictional — Jeff will replace with real projects before DNS cutover (story_012). The `picsum.photos` URLs are stable seeded random images; they require internet at page-load time, which is fine for the dry run.

**Implementation notes**:
- Preserve the existing `georgia-soul.md` content exactly. Only append the new `## Guardrails` section.

**Acceptance criteria**:
- [ ] `georgia-soul.md` exists in `oops-all-vibes/`, contains the original draft plus the new `## Guardrails` section
- [ ] `facts.json` is valid JSON (`python -c "import json; json.load(open('facts.json'))"` exits 0)
- [ ] `facts.json` contains exactly 10 projects
- [ ] Original `georgia-soul.md` at Cowork OS root is deleted (moved, not copied)

**Out of scope**:
- Writing real project descriptions
- Any pipeline logic

---

## story_003 — Prompt assembly script

**Goal**: A Python script that assembles Georgia's full prompt from the 4 layers and prints it to stdout.

**Depends on**: story_002

**Files to create**:
- `oops-all-vibes/scripts/assemble_prompt.py`
- `oops-all-vibes/scripts/__init__.py` (empty)
- `oops-all-vibes/requirements.txt` — start with: `anthropic>=0.40.0`, `beautifulsoup4>=4.12.0`, `python-frontmatter>=1.0.0`, `requests>=2.31.0`. Add deps as later stories need them.

**Tunable constants** (expose at top of `assemble_prompt.py`):

```python
RECENCY_WINDOW_DAYS = 14      # entries this fresh are always included verbatim
OLDER_TOP_N = 20              # max number of older entries to include (scored)
IMPORTANCE_DECAY_DAYS = 180   # half-life ≈ 125 days
DEFAULT_IMPORTANCE = 2        # fallback when an entry has no importance tag
```

**Diary entry format** (what Georgia writes; what this script parses):

Each file in `log/YYYY-MM-DD.md` is markdown with YAML frontmatter:

```markdown
---
date: 2026-04-22
importance: 3
---

Today I built something. Here's what I was going for...
```

Use `python-frontmatter` to parse. If frontmatter is missing or `importance` is absent/invalid, default to `DEFAULT_IMPORTANCE`.

**Behavior**:

The script reads, in order:

1. **Layer 1 (Soul)**: full contents of `georgia-soul.md`.
2. **Layer 2 (Facts)**: full contents of `facts.json` (embed as a code block).
3. **Layer 3 (History)**: all files in `log/` matching `YYYY-MM-DD.md`. Split into two bundles by age relative to the run date:
   - **Recent bundle**: all entries dated within the last `RECENCY_WINDOW_DAYS` days. Include verbatim (frontmatter + body), oldest first. These are Georgia's vivid working memory.
   - **Older bundle**: for every entry older than the recency window, compute `score = importance * exp(-days_ago / IMPORTANCE_DECAY_DAYS)`. Take the top `OLDER_TOP_N` by score. Order the selected entries oldest → newest in the prompt.
   - If both bundles are empty: insert the day-1 sentinel (below) and skip both bundles.
4. **Layer 4 (Feedback)**: look for `feedback/<yesterday>.json`. If present, render the narrative block (below). If absent, choose sentinel based on whether `archive/` has any prior entries.

**Feedback rendering** (when `feedback/<yesterday>.json` is present):

Parse the JSON and render a human-readable block. Use `null`-safe phrasing — any field can be missing or null.

```
Yesterday's feedback (2026-04-21):

People: 142 visitors looked at your work yesterday. 289 pageviews total.
Recent: In the last 7 days, 487 people came through, averaging about 70 a day. Over 30 days, 1,204 visitors, averaging 40.
Historical: 8,430 total visitors across 68 days of you being awake. Your peak day was 2026-03-15 with 512 visitors.
Trend: Yesterday was 2.04× your 7-day average. Week-over-week, traffic is up 32%.
```

Append `jeff_note` section only if non-null:
```
Jeff says: <the note content>
```

If any numeric fields are null/missing, omit that line entirely rather than printing "null visitors." Phrase gracefully when peak_day or trend data isn't available yet.

**No-feedback sentinels** (used when `feedback/<yesterday>.json` is absent):

If `archive/` contains zero prior entries (true day 1):
```
[feedback]
This is your first day online. There's no feedback yet because no one has seen your work. You are waking up.
[/feedback]
```

If `archive/` has entries but the feedback file is missing (fetcher failed for some reason):
```
[feedback]
The data wasn't available today — the analytics pipeline didn't deliver. You're building blind.
[/feedback]
```

**Prompt structure** (what the final assembled prompt looks like):

```
You are Georgia. Read this carefully.

[Layer 1: full soul doc]

These are the facts about Jeff. They are inviolable — every version of the site must include them, however creatively presented.

[Layer 2: facts.json as a code block]

---

Recent history — the last 14 days, fresh in your mind:

[Layer 3 Recent bundle, each entry as:
## YYYY-MM-DD (importance: N)
<body>
]

Older — things you still think about, surfaced because they mattered:

[Layer 3 Older bundle, same format. Omit section if empty.]

---

[Layer 4: feedback narrative OR sentinel]

---

Today is {YYYY-MM-DD}.

Your task:
1. Build today's site. Output the full HTML (doctype through </html>) inside <site>...</site> tags.
2. Write your log entry for today. Output inside <log>...</log> tags. The log must be markdown with YAML frontmatter exactly like this:

   ---
   date: {YYYY-MM-DD}
   importance: <1-5>
   ---

   <your diary content>

   Importance scale: 1 = routine day. 2 = ordinary. 3 = memorable. 4 = significant. 5 = a day that defined something about you. Be honest. Most days are 1 or 2.

Remember: the facts above are inviolable. Everything else — tone, design, copy, structure — is yours.
```

**Day-1 sentinel (when Layer 3 has zero entries)**:
```
[history]
This is your first day. You have no prior entries. You are just waking up.
[/history]
```

**Implementation notes**:
- Use `pathlib` for file operations, `python-frontmatter` for parsing diary entries
- Script accepts `--date YYYY-MM-DD` optional arg (default: today in UTC). Enables testability.
- Script prints assembled prompt to stdout; no side effects
- When importance tag is malformed (non-integer, out of 1–5 range, missing): log a warning to stderr, use default
- Feedback rendering is defensive: any missing field in the JSON produces a graceful omission, not a crash
- Keep the scoring function simple and readable — a future Jeff (or Georgia) should be able to eyeball it and see why any given entry was selected

**Acceptance criteria**:
- [ ] `python scripts/assemble_prompt.py` prints a prompt containing soul, facts, history section (or day-1 sentinel), feedback section (narrative or sentinel), and the invocation with importance-tagging instructions
- [ ] `python scripts/assemble_prompt.py --date 2026-01-01` works with no log/feedback files and emits both sentinels
- [ ] With a valid feedback JSON file, the narrative block appears with numbers interpolated correctly
- [ ] With a feedback JSON missing `trend.week_over_week_pct`, the trend line still renders without crashing (omits or reshapes the missing field)
- [ ] With 5 files in `log/` all within 14 days of run date: all 5 appear in the Recent bundle, Older bundle is empty (section omitted)
- [ ] With 3 files within 14 days and 50 older files: Recent has 3, Older has at most 20 (top-scored)
- [ ] Older-bundle selection respects importance: given files A (100 days old, importance 5) and B (30 days old, importance 1), A should rank above B
- [ ] Malformed frontmatter (missing `importance`) falls back to default and logs warning; run does not crash
- [ ] Unit test for the scoring function with known inputs
- [ ] Unit test for feedback narrative rendering with full and partial JSON inputs

**Out of scope**:
- Fetching feedback (that's story_009)
- Calling Sonnet
- Writing any output files

---

## story_004 — Sonnet call with structured output

**Goal**: Script that takes an assembled prompt, calls Sonnet 4.6, and returns Georgia's two outputs: HTML and diary entry.

**Depends on**: story_003

**Files to create**:
- `oops-all-vibes/scripts/call_sonnet.py`

**Behavior**:

- Function `call_sonnet(prompt: str) -> tuple[str, str]`: returns `(html, diary)`
- Uses `anthropic.Anthropic()` client; model `claude-sonnet-4-6`; `max_tokens=8000` (plenty for a single-page site + diary)
- Reads `ANTHROPIC_API_KEY` from environment
- Parses response: extract text between `<site>...</site>` and `<log>...</log>` tags
- If either tag is missing or empty, raise `SonnetOutputError` with the raw response captured

**Implementation notes**:
- Use a regex or a simple string-slicing parse — do not use full XML parser (Georgia's output inside `<site>` will contain `<html>` etc., which would break XML parsers)
- Pattern: `re.search(r'<site>(.*?)</site>', text, re.DOTALL)` and same for `<log>`
- On API error (any `anthropic.APIError` subclass, including rate limits), let the exception propagate — the retry/fail-open logic lives in story_006, not here
- Keep this module pure: no file I/O, no git

**Acceptance criteria**:
- [ ] With `ANTHROPIC_API_KEY` set, `python -c "from scripts.call_sonnet import call_sonnet; print(call_sonnet('Say hi inside <site> tags and bye inside <log> tags.'))"` returns a `(str, str)` tuple with both non-empty
- [ ] `SonnetOutputError` raised when response lacks required tags (test with a mocked client)
- [ ] API errors propagate (not caught)

**Out of scope**:
- Validation of Georgia's output (story_005)
- Retry logic (story_006)
- Prompt caching — not worth the complexity for 1 call/day

---

## story_005 — Output validation (HTML + diary)

**Goal**: A function that checks both Georgia's HTML output and her diary output for correctness, returning a combined structured report.

**Depends on**: story_002 (needs `facts.json`)

**Files to create**:
- `oops-all-vibes/scripts/validate_output.py`

**Behavior**:

Function `validate_output(html: str, diary: str, facts: dict, today: str) -> tuple[bool, list[str]]`:

Returns `(is_valid, failure_reasons)`. Runs both HTML and diary checks and collects ALL failures — does not short-circuit. Each failure is a human-readable string that can be shown to Georgia verbatim as a retry hint.

**HTML checks**:
1. **Parses as HTML**: `BeautifulSoup(html, 'html.parser')` succeeds and has a `<body>` tag.
2. **Nontrivial content**: `<body>` text content has at least 200 characters (after whitespace collapse).
3. **Inviolable facts present** (case-sensitive substring match in raw HTML):
   - `facts["name"]`
   - `facts["email"]`
   - `facts["linkedin_url"]`
   - Each project title from `facts["projects"]`
4. **Size sane**: total HTML between 1KB and 500KB.

**Diary checks**:
5. **Has YAML frontmatter**: the diary string starts with `---\n`, then YAML key-value pairs, then `---\n`.
6. **`date` field matches today**: frontmatter `date` parses to a date equal to the `today` argument.
7. **`importance` field valid**: frontmatter contains `importance`, parses as integer, in range 1–5 inclusive.
8. **Non-empty body**: content after the closing `---` has at least 20 characters.

**Failure phrasing** (must be usable as a retry hint back to Georgia — first-person, plain):

Examples:
- `"You did not include Jeff's email address (jeff@clarkle.com). This is an inviolable fact."`
- `"Your diary is missing the YAML frontmatter block. It must start with ---, include date and importance, then end with ---."`
- `"Your diary's importance value (7) is out of range. Must be an integer 1-5."`
- `"Your diary's date (2026-04-21) doesn't match today's date (2026-04-22). Set date correctly in the frontmatter."`

**Implementation notes**:
- Use BeautifulSoup for HTML parse check, `python-frontmatter` for diary parse check
- For fact presence, use raw-text substring (Georgia might put facts in attributes, alt text, data-*, etc. — permissive match is correct)
- Collect all failures — don't short-circuit. Georgia gets a complete picture on retry.
- If HTML fails to parse at all, still attempt the diary checks (independent) — but some HTML checks (facts present, size) will be skipped if parse fails, and those skipped checks should produce their own failure strings so Georgia knows to fix the parse first

**Acceptance criteria**:
- [ ] Valid HTML + valid diary returns `(True, [])`
- [ ] HTML missing the email returns `(False, [<string mentioning email>])`
- [ ] Diary missing frontmatter returns `(False, [<string mentioning frontmatter>])`
- [ ] Diary with `importance: 7` returns `(False, [<string mentioning importance range>])`
- [ ] Diary with wrong date returns `(False, [<string mentioning date mismatch>])`
- [ ] Both HTML and diary invalid returns `(False, [...])` with ALL failure strings collected (multiple failures)
- [ ] Unit tests exist for each failure mode

**Out of scope**:
- Deciding what to do when invalid — that's story_006

---

## story_006 — Run orchestration: assembly + call + validation + retry

**Goal**: The top-level daily pipeline script that runs end-to-end and handles failure modes per Jeff's spec (two strikes, then leave yesterday's site up).

**Depends on**: story_003, story_004, story_005

**Files to create**:
- `oops-all-vibes/scripts/run_georgia.py`

**Behavior**:

```
main():
    today = date.today().isoformat()
    start = time.monotonic()
    attempts = 0
    validation_failures = []
    api_errors = 0
    committed = False

    prompt = assemble_prompt(today)

    for attempt in (1, 2):
        attempts = attempt
        try:
            html, diary = call_sonnet(prompt)
        except (APIError, RateLimitError) as e:
            api_errors += 1
            log_to_stderr(f"API error on attempt {attempt}: {e}")
            record_stats(today, attempts, validation_failures, api_errors, committed, start)
            sys.exit(1)
        except SonnetOutputError as e:
            # Missing tags — treat like validation failure with a specific hint
            reasons = ["Your previous response didn't include the <site>...</site> or <log>...</log> tags correctly. Both are required."]
            validation_failures.append(reasons)
            if attempt == 1:
                prompt = add_retry_hint(prompt, reasons)
                continue
            record_stats(today, attempts, validation_failures, api_errors, committed, start)
            sys.exit(1)

        is_valid, reasons = validate_output(html, diary, facts, today)
        if is_valid:
            write_outputs(today, html, diary, prompt)
            committed = True
            record_stats(today, attempts, validation_failures, api_errors, committed, start)
            return

        validation_failures.append(reasons)
        if attempt == 1:
            prompt = add_retry_hint(prompt, reasons)
            continue

        # Attempt 2 also failed → fail-open path
        log_to_stderr(f"Validation failed twice. Latest reasons: {reasons}")
        record_stats(today, attempts, validation_failures, api_errors, committed, start)
        sys.exit(1)

def add_retry_hint(prompt: str, reasons: list[str]) -> str:
    return prompt + f"""

[validation-failure]
Your previous attempt failed these checks:
{chr(10).join(f"- {r}" for r in reasons)}

Try again. Fix these issues. Note the mishap somewhere in your diary entry for today — own it.
[/validation-failure]
"""
```

**Implementation notes**:
- `record_stats` is a function imported from the observability module (story_011). If story_011 hasn't been implemented yet, leave a `# TODO(story_011)` stub so the structure is clear but execution still proceeds.
- The `SonnetOutputError` case gets one retry hint — same pattern as validation failure. The retry hint tells Georgia which tag was missing.
- Exit codes: 0 = success (files written and committed); 1 = failure (no commit, yesterday's site stays live).
- `write_outputs` is implemented in story_007.
- Log failures to stderr so GitHub Actions surfaces them in the run log.

**Acceptance criteria**:
- [ ] With a mocked `call_sonnet` returning valid output first try: exits 0, `write_outputs` called once
- [ ] With mocked `call_sonnet` returning invalid HTML twice: exits 1, `write_outputs` NOT called, stats record shows `attempts=2, committed=false`
- [ ] With mocked `call_sonnet` raising `APIError`: exits 1, no retry attempted, stats record shows `api_errors=1, committed=false`
- [ ] With mocked `call_sonnet` raising `SonnetOutputError` first, returning valid second: exits 0, committed, prompt on second call includes a `<site>/<log>` tag hint
- [ ] With mocked `call_sonnet` returning invalid diary first (bad frontmatter), valid second: exits 0, committed, retry prompt contains the diary failure string
- [ ] Stats record is written to `stats.jsonl` on every exit path (success AND failure)

**Out of scope**:
- Writing files and committing (that's story_007)
- GitHub Actions configuration
- Stats file creation (that's story_011, but this story defines the call)

---

## story_007 — Commit pipeline: write files and rebuild archive index

**Goal**: On successful generation, write all outputs to disk, regenerate the archive index page, and commit.

**Depends on**: story_006

**Files to create**:
- `oops-all-vibes/scripts/write_outputs.py`
- `oops-all-vibes/scripts/build_archive_index.py`

**Behavior**:

`write_outputs(date_str, html, diary, prompt)`:
1. Call `inject_tech(html, date_str, ...)` from story_010 (if the module exists; if not, skip — story_010 will wire it in when built).
2. Write `index.html` (today's site).
3. Write `archive/<date_str>.html` (same content, permanent copy).
4. Write `log/<date_str>.md` (Georgia's diary entry — contains her own frontmatter; just save verbatim).
5. Write `prompts/<date_str>.md` (the full assembled prompt — transparency).
6. Call `build_archive_index()` (below).
7. `git add -A && git commit -m "Georgia, {date_str}"` and `git push origin main`.

`build_archive_index()`:
- Lists all files in `archive/*.html` sorted reverse-chron
- Writes `archive/index.html` — a plain, non-chaotic HTML page (navigation, not art)
- Template: title "Archive — oops-all-vibes", then a simple `<ul>` with each date linked to its file, plus a link back to `/` (today's site)
- This page stays consistent across days; Georgia does not reimagine it

**Implementation notes**:
- The archive index is deliberately boring — it's infrastructure. Keep it under 30 lines of HTML.
- Commit happens only when called from `run_georgia.py` after successful validation. Skip the commit when running locally for testing (add a `--no-commit` flag).
- Use `subprocess.run(["git", ...])` — don't pull in a Python git library.
- If `git push` fails (network, auth): log error, exit 1. Files remain on disk; Jeff can push manually.

**Acceptance criteria**:
- [ ] After `write_outputs("2026-04-22", "<html>...</html>", "diary text", "prompt text")`: `index.html`, `archive/2026-04-22.html`, `log/2026-04-22.md`, `prompts/2026-04-22.md` all exist with correct content
- [ ] `archive/index.html` exists and contains a link to `2026-04-22.html`
- [ ] Running on a day when `archive/2026-04-20.html` and `archive/2026-04-21.html` already exist: `archive/index.html` lists all three in reverse-chron order
- [ ] With `--no-commit` flag: no `git commit` is invoked (testable with a mocked subprocess)
- [ ] Without the flag: a commit and push occur

**Out of scope**:
- Injecting GoatCounter (story_010)
- Injecting the prompt-link footer (story_010)
- Stats page generation (story_011)

---

## story_008 — GitHub Actions daily cron workflow

**Goal**: A workflow that runs the full pipeline daily at ~3am ET: fetch feedback → run Georgia → stats.

**Depends on**: story_006, story_007, story_009 (for the feedback fetcher call), story_011 (for stats)

**Files to create**:
- `oops-all-vibes/.github/workflows/daily-georgia.yml`

**Workflow content**:

```yaml
name: Daily Georgia
on:
  schedule:
    - cron: '0 7 * * *'  # ≈3am EST / 2am EDT
  workflow_dispatch:      # allow manual trigger

permissions:
  contents: write          # so the Action can commit

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - name: Fetch yesterday's feedback from GoatCounter
        env:
          GOATCOUNTER_API_KEY: ${{ secrets.GOATCOUNTER_API_KEY }}
          GOATCOUNTER_CODE: ${{ vars.GOATCOUNTER_CODE }}
        run: python scripts/fetch_feedback.py
        continue-on-error: true   # failure here should not block Georgia
      - name: Run Georgia
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          GOATCOUNTER_CODE: ${{ vars.GOATCOUNTER_CODE }}
        run: |
          git config user.name "Georgia"
          git config user.email "georgia@clarkle.com"
          python scripts/run_georgia.py
```

**Manual setup (document for Jeff, before first run)**:
- Add `ANTHROPIC_API_KEY` as a repo secret
- Add `GOATCOUNTER_API_KEY` as a repo secret (obtained in story_009 setup)
- Add `GOATCOUNTER_CODE` as a repo variable (non-secret)

**Implementation notes**:
- The `continue-on-error: true` on the feedback fetcher step is deliberate: if the fetcher fails (API down, rate limit, day 1), Georgia still runs, the assembler just uses the no-feedback sentinel.
- `run_georgia.py` commits and pushes internally — the workflow doesn't need a separate push step.
- The story should not commit secrets anywhere.

**Acceptance criteria**:
- [ ] Workflow file exists at `.github/workflows/daily-georgia.yml`
- [ ] Manual trigger (`gh workflow run daily-georgia.yml`) executes successfully once all secrets are set
- [ ] On success, a commit appears on `main` authored by `Georgia <georgia@clarkle.com>`
- [ ] On API error, workflow status is red, no commit is made
- [ ] Feedback fetcher failure does not cause the Georgia step to be skipped

**Out of scope**:
- GitHub Pages configuration (story_012)
- Any custom domain setup (story_012)

---

## story_009 — GoatCounter data fetcher

**Goal**: Before each Georgia run, fetch visitor analytics from GoatCounter and write a structured feedback file that Layer 4 will consume. This closes the loop between what Georgia makes and whether anyone's watching.

**Depends on**: story_001 (only needs the repo — reads/writes to `feedback/`)

**Files to create**:
- `oops-all-vibes/scripts/fetch_feedback.py`

**Setup (manual, for Jeff — document as story prerequisite)**:
- Create a GoatCounter account at https://www.goatcounter.com
- Register a site code (suggestion: `oops-all-vibes`) — this gives you the code string used in the tracking script
- Generate an API token in GoatCounter settings
- Add `GOATCOUNTER_API_KEY` as a GitHub Actions secret
- Add `GOATCOUNTER_CODE` as a GitHub Actions variable (not a secret — site codes are public)

**Behavior**:

`fetch_feedback(run_date: date) -> dict | None`:

1. Read `GOATCOUNTER_API_KEY` and `GOATCOUNTER_CODE` from env. If either missing, log warning to stderr, return `None`.
2. Determine yesterday = `run_date - 1 day`.
3. If there are zero files in `archive/`, this is day 1 — nothing to fetch. Log and return `None`.
4. Query GoatCounter API (base URL `https://{code}.goatcounter.com/api/v0`; auth header `Authorization: Bearer {API_KEY}`). GoatCounter's API docs: https://www.goatcounter.com/api. Aim for `/stats/total` and `/stats/hits` endpoints, or the simpler `/export` if it's easier.
5. Compute the fields in the schema at the top of this doc:
   - `yesterday.visitors`, `yesterday.pageviews`
   - `recent.last_7_days_visitors`, `recent.last_7_days_avg`
   - `recent.last_30_days_visitors`, `recent.last_30_days_avg`
   - `historical.all_time_visitors`
   - `historical.days_live` — count of files in `archive/` (proxy for number of days Georgia's been live)
   - `historical.peak_day.{date, visitors}` — day with highest visitor count; null if no data
   - `trend.yesterday_vs_7d_avg` — yesterday's visitors / 7-day avg; null if 7-day avg is 0 or null
   - `trend.week_over_week_pct` — percent change: this week's 7-day total vs previous 7-day total; null if insufficient data
6. Write the dict to `feedback/<yesterday>.json`.
7. On any API error: log the error, return `None`, do not crash.

**CLI**:
- `python scripts/fetch_feedback.py` (no args) — runs for today, fetching yesterday's data, writes the feedback file

**Implementation notes**:
- Use `requests` (added to `requirements.txt` in story_003)
- Never raise on API error — the pipeline is resilient to a missing feedback file
- The feedback file is named by the DATE THE DATA DESCRIBES (yesterday), not the run date
- `days_live` as archive file count is a reasonable proxy; if there are gaps (days the pipeline failed), this slightly undercounts, which is fine
- Expose the GoatCounter base URL and endpoints as module-level constants so they're easy to change if GoatCounter's API evolves
- If `requests` import or env vars are missing, fail gracefully with a stderr message — never leave a partial/malformed JSON file behind

**Acceptance criteria**:
- [ ] With both env vars set and a live GoatCounter account with real traffic: running the script writes a valid JSON file at `feedback/<yesterday>.json` matching the schema
- [ ] With `GOATCOUNTER_API_KEY` unset: no file written, warning logged to stderr, exit code 0
- [ ] With `GOATCOUNTER_CODE` unset: same as above
- [ ] With zero files in `archive/`: no file written (day-1 short-circuit), exit code 0
- [ ] With a mocked HTTP 500 response: no file written (or partial file cleaned up), warning logged, exit 0
- [ ] With a mocked successful response: file is valid JSON, all schema fields present (null where data insufficient)
- [ ] `peak_day` correctly identifies the max-visitor day from the mocked response
- [ ] `trend.week_over_week_pct` correctly computed from mocked 14-day data; null when <14 days available

**Out of scope**:
- Fetching or writing `jeff_note` (stays null until a Jeff-note mechanism is built)
- Any UI or dashboard for the data
- Retrying API calls (single attempt is fine; tomorrow will try again)

---

## story_010 — Client-side tracker and prompt-link injection

**Goal**: Post-hoc inject the GoatCounter tracking snippet and the "today's prompt" footer link into Georgia's HTML before it's written to disk. This keeps tech concerns out of Georgia's job while guaranteeing they appear on every page.

**Depends on**: story_007 (wires into `write_outputs`)

**Files to create/modify**:
- New: `oops-all-vibes/scripts/inject_tech.py`
- Modify: `scripts/write_outputs.py` to call the injector before writing

**Behavior**:

`inject_tech(html: str, date_str: str, goatcounter_code: str | None) -> str`:

1. Parse with BeautifulSoup.
2. If no `<head>`: create one inside `<html>`.
3. If `goatcounter_code` is not `None`, append to `<head>`:
   ```html
   <script data-goatcounter="https://{code}.goatcounter.com/count"
           async src="//gc.zgo.at/count.js"></script>
   ```
4. Append to end of `<body>`:
   ```html
   <footer style="position:fixed;bottom:4px;right:8px;font-size:10px;opacity:0.5;font-family:sans-serif;z-index:9999;">
     <a href="/prompts/{date_str}.md" style="color:inherit;">today's prompt</a>
   </footer>
   ```
5. Return the modified HTML as a string.

`write_outputs` is updated to call `inject_tech(html, date_str, os.environ.get("GOATCOUNTER_CODE"))` before writing `index.html` and `archive/<date>.html`.

**Implementation notes**:
- The footer is intentionally unobtrusive. Georgia may or may not acknowledge its existence. That's fine.
- If `GOATCOUNTER_CODE` is unset (local dev without the env var), skip the script injection with a warning — the footer still gets injected.
- This injection happens AFTER Sonnet generation but BEFORE the output is validated. Wait — actually no: validation runs on Georgia's raw output (in story_006), BEFORE this injection. The injection is purely cosmetic and must not affect validation. So order is: validate → inject → write. Confirm this flow is honored.
- The GoatCounter tracking script itself is client-side only — it fires on page load and sends data TO GoatCounter. This is the write-side of the analytics loop. The READ side is story_009.

**Acceptance criteria**:
- [ ] `inject_tech(html, "2026-04-22", "oops-all-vibes")` returns HTML containing the GoatCounter script tag and the footer
- [ ] `inject_tech(html, "2026-04-22", None)` returns HTML containing the footer but NO script tag
- [ ] Result still parses as valid HTML
- [ ] `write_outputs` calls `inject_tech` between validation and writing to disk
- [ ] Today's `prompts/<date>.md` is reachable from the live site via the footer link
- [ ] Injected content doesn't trigger any story_005 validation failure (irrelevant if order is validate-then-inject, but confirm)

**Out of scope**:
- Fetching analytics data (story_009)
- Reading analytics back for Georgia (story_009)

---

## story_011 — Pipeline observability (stats.jsonl + stats.html)

**Goal**: Track pipeline health over time so silent degradation becomes visible. After a month, Jeff can see how often Georgia failed validation, how often the retry saved the run, how often everything fell through to leave-yesterday-up.

**Depends on**: story_006 (the run orchestrator calls `record_stats`)

**Files to create/modify**:
- New: `oops-all-vibes/scripts/record_stats.py`
- New: `oops-all-vibes/scripts/build_stats_page.py`
- New files produced at runtime: `oops-all-vibes/stats.jsonl`, `oops-all-vibes/stats.html`

**Behavior**:

`record_stats(date: str, attempts: int, validation_failures: list[list[str]], api_errors: int, committed: bool, start_time: float) -> None`:

1. Compute `duration_ms = int((time.monotonic() - start_time) * 1000)`
2. Flatten validation_failures into a single list of reason strings: `[r for attempt in validation_failures for r in attempt]`
3. Append one JSON object per line to `stats.jsonl`:
   ```json
   {"date": "2026-04-22", "attempts": 2, "validation_failures": ["missing email", "importance out of range"], "api_errors": 0, "committed": true, "duration_ms": 7340}
   ```
4. Call `build_stats_page()`.

`build_stats_page() -> None`:

1. Read `stats.jsonl`.
2. Filter to last 30 entries (by file order — append-only guarantees chronological).
3. Compute top-of-page summary: over the last 30 runs — `runs_total`, `first_try_success_pct`, `overall_commit_pct`, `avg_duration_s`.
4. Write `stats.html`:
   - Plain, no JavaScript, no external CSS, no images
   - Top section with the summary stats
   - Table with columns: date, attempts, committed ✓/✗, validation failures (truncated to 50 chars), api errors, duration (s)
   - Rows styled subtly so failed runs stand out (red background or similar — minimal CSS inline)
   - Link back to `/` at the bottom

**Implementation notes**:
- `stats.jsonl` is append-only. Never rewrite it. If the file doesn't exist, create it on first call.
- `stats.html` is regenerated every run. Keep it under 150 lines total.
- Both files are committed alongside Georgia's output by the normal `git add -A` in story_007's commit step — no special handling needed.
- Do not rely on JavaScript or external assets for the stats page. It should load instantly even in a constrained browser.
- `record_stats` is called from `run_georgia.py` (story_006). If an early exit happens before any attempts, still record with `attempts=0`, `committed=false`.

**Acceptance criteria**:
- [ ] After 3 runs, `stats.jsonl` has exactly 3 lines, each valid JSON matching the schema
- [ ] `stats.html` exists after each run
- [ ] Summary stats on `stats.html` are correct for a known input (unit test with a canned `stats.jsonl`)
- [ ] Table displays the last 30 entries in reverse-chronological order (newest at top)
- [ ] Failed runs are visually distinguishable from successful runs
- [ ] Page renders correctly in a browser with JavaScript disabled
- [ ] `/stats.html` is reachable from the live site (once deployed in story_012)

**Out of scope**:
- Alerting (email/Slack on failures) — GitHub Actions email notifications are enough for v1
- Richer visualization (charts, trends)
- Historical aggregations beyond 30 days on the rendered page (the jsonl retains everything)

---

## story_012 — Dry run and DNS cutover (manual — Jeff)

**Goal**: Jeff personally validates that Georgia works end-to-end, then points `clarkle.com` at the GitHub Pages deployment.

**Depends on**: all prior stories

**This story is manual work for Jeff. Claude Code completes the one technical prerequisite (the `CNAME` file) and produces the checklist below for Jeff to execute.**

**Technical prerequisite (Claude Code)**:
- Create `oops-all-vibes/CNAME` containing exactly `clarkle.com` (single line, no trailing newline)
- Commit but do NOT push until Jeff confirms dry run success

**Jeff's checklist**:

1. Ensure `ANTHROPIC_API_KEY` is set as a GitHub Actions secret (repo settings → Secrets)
2. Ensure `GOATCOUNTER_API_KEY` is set as a GitHub Actions secret
3. Ensure `GOATCOUNTER_CODE` is set as a GitHub Actions variable
4. Locally in `oops-all-vibes/`, set `ANTHROPIC_API_KEY` (and optionally `GOATCOUNTER_CODE` for the script tag) in env, then run `python scripts/run_georgia.py` 2–3 times — review each output in a browser (`open index.html`)
5. Check `stats.html` — confirm the run shows up with `committed: true`
6. If outputs look good: push to `main`, enable GitHub Pages (repo settings → Pages → Source: `main` branch, root)
7. Visit `https://jeffclark.github.io/oops-all-vibes/` — confirm the site renders
8. Trigger the workflow manually (`gh workflow run daily-georgia.yml`) and confirm it runs green
9. Wait through one real 3am-ish run and eyeball the result the next morning
10. Check `https://jeffclark.github.io/oops-all-vibes/stats.html` — confirm the scheduled run is recorded
11. When satisfied, push the `CNAME` file (from the technical prerequisite above)
12. In the registrar for `clarkle.com` (you know where it is), add these DNS records:
    - `A` records for apex → GitHub Pages IPs: `185.199.108.153`, `185.199.109.153`, `185.199.110.153`, `185.199.111.153`
    - `CNAME` for `www` → `jeffclark.github.io`
    - **Do not touch** existing MX records, `coach.clarkle.com`, or any other existing subdomains
13. In GitHub Pages settings, enter `clarkle.com` as the custom domain; enable "Enforce HTTPS" once the cert provisions
14. Wait up to an hour for DNS + cert
15. Visit `https://clarkle.com` — confirm Georgia's latest site renders

**Acceptance criteria**:
- [ ] `CNAME` file exists in the repo (staged but not pushed until Jeff approves)
- [ ] Jeff signs off that 2–3 local runs look good
- [ ] Jeff has replaced placeholder projects in `facts.json` with real ones (or explicitly keeps placeholders for the first live week as art)
- [ ] `https://clarkle.com` serves Georgia's site with a valid HTTPS cert
- [ ] `https://clarkle.com/stats.html` renders the observability page
- [ ] Existing DNS records (MX, `coach.` subdomain, others) still resolve correctly post-cutover

**Out of scope**:
- Automating any DNS work
- Any post-launch iteration

---

## Post-launch (not in this story set)

These are explicitly out of scope for v1, per the PRD and your accepted flags:

- Voting / theme selection mechanic
- Build-in-public log (moved to "What This Is NOT (yet)")
- iMessage or Slack publishing workflow (including the `jeff_note` population)
- News / current-events awareness (Layer 5)
- "Chat with AI Jeff" feature
- Interactive portfolio with live demos

---

## Handoff notes for Claude Code

- Pick stories in order (the dependency graph shows the critical path). story_009 (feedback fetcher) and story_011 (observability) can be built in parallel with story_007 if you want; story_010 waits for story_007; story_008 waits for story_006, 007, 009, 011; story_012 is always last.
- Each story's acceptance criteria are the done-ness bar. Don't add polish beyond them.
- When running on 2026-04-22 (day 1), both the day-1 history sentinel AND the day-1 feedback sentinel fire automatically — no special-casing needed in Jeff's setup.
- If anything is ambiguous mid-implementation, prefer asking Jeff over guessing. This is a personal site tied to his name.
- Two hygiene checks before marking anything "done": (a) does the script gracefully survive missing env vars and missing files, and (b) does an unhappy path still produce a clean commit history (no half-written files, no broken state)?
- Stories 013–018 (the corpus set) are appended at the end of this file and have their own dependency graph and shared-facts block. They post-date launch; 013–015 run offline on Jeff's machine, only 016 touches the cron.

---

# Corpus Story Set (story_013 – story_018)

Georgia asked for "a corpus I can have taste about" — a finite visual set she sees
repeatedly, so preference can form from being moved by specific things rather than
from reading descriptions of people being moved. This story set builds that.

**Source material**: marching band / drum corps full-show video, supplied by Jeff.
**Why it fits**: drill is composition on a 100-yard grid, designed to be read from
above as a static form. A press-box frame is already a legible visual artifact.
**What she cannot have**: sound. She gets the *shape* of the audio (loudness and
tempo over time, rendered as an image) alongside frames from the same timestamps.
No transcripts, no commentary, no fan reactions — those are descriptions, which is
the exact thing she named as the problem.

## The split (per CLAUDE.md)

Chaos is hers; the pipeline is boring. Stories 013–015 run **offline, once**, on
Jeff's machine. Only story_016's selection step runs inside the 3am cron, and it
must fail open: if anything about the corpus is broken or missing, the day still
ships text-only.

## Corpus Dependency Graph

```
013 (ingest: video → frames + audio shape)
      │
      ▼
014 (curation session: Georgia picks her own shelf)
      │
      ▼
015 (Files API upload + public manifest)
      │
      ▼
016 (daily selection + multimodal call)  ──►  017 (preference log: <taste> tag)
                                                      │
                                                      ▼
                                              018 (anchor drift: consistency over time)
```

## Corpus Facts (shared context for stories 013–018)

- **Frame size**: 1024×576 JPEG. Costs `ceil(1024/28) × ceil(576/28)` = 37 × 21 =
  **777 visual tokens**. 640×360 would be 299 tokens, but at that size a 150-person
  field form is a few pixels per performer and drill stops being legible. Cost
  difference is fractions of a cent a day; legibility is the whole product. Do not
  downsize to save tokens.
- **Daily image budget**: **20 images**. 10 anchors + 6 rotating frames + up to 4
  show-shape plots. ~14.5k input tokens ≈ **$0.08/day** at Opus 5's $5/1M.
  This cap is **ours, not the API's**. The API applies a stricter per-image
  dimension rule above 20 image blocks, but only to images over 2000 px on a side —
  ours are 1024×576 and 1024×384, so we would clear it either way. 20 is a cost and
  attention budget, chosen because a set she can actually hold in mind beats a set
  she skims. There is headroom if a later story earns it.
- **Corpus size**: 8–12 shows × 10 keepers = **80–120 frames**, plus one show-shape
  plot per show. Finite is the feature. Do not grow this later without a story.
- **Anchors vs rotating**: 10 anchor frames are shown **every single day, forever**.
  Repetition is the mechanism — a preference cannot form from a single viewing.
- **Storage**: keeper frames live in the Anthropic Files API, never in this repo.
  Files persist until deleted (no expiry unless one is set at upload); upload,
  storage, listing and deletion are free; only tokens are billed at use. The public
  repo holds the manifest — source URL, timestamp, corps, year — so the corpus is
  fully reproducible by anyone without clarkle.com rehosting copyrighted footage.
- **Beta header**: referencing files by `file_id` needs `files-api-2025-04-14`.
  `call_model.py` is already on `client.beta.messages.stream`, so this is an append
  to the existing `betas` list, not a migration.

---

## story_013 — Corpus ingest: video → candidate frames + audio shape

**Goal**: An offline script that turns a supplied show video into (a) a set of
candidate frames, (b) one image showing the show's dynamic arc, and (c) contact
sheets Jeff can eyeball before any curation happens. Runs on Jeff's machine, once
per show. Never runs in CI.

**Depends on**: nothing in this set (needs only the repo)

**Files to create**:
- `scripts/corpus/__init__.py` (empty)
- `scripts/corpus/ingest.py`
- `requirements-corpus.txt` — `yt-dlp`, `librosa`, `matplotlib`, `numpy`, `Pillow`.
  **Separate from `requirements.txt` on purpose**: the daily Actions run must not
  install librosa. Nothing in this file may ever be imported by the daily pipeline.
- `.gitignore` — add `corpus/raw/`

**System prerequisite (manual, Jeff)**: `ffmpeg` and `ffprobe` on PATH.
Script must check for both at startup and exit with a clear message if missing.

**Input**: `corpus/sources.json`, hand-written by Jeff, committed. One entry per show:

```json
[
  {
    "show_id": "bd-2014",
    "url": "https://www.youtube.com/watch?v=...",
    "corps": "Blue Devils",
    "year": 2014,
    "angle": "press-box",
    "axis_tags": ["style:dci", "form:asymmetric", "era:2010s"]
  }
]
```

`angle` must be `press-box` or `high`. **A `field-level` entry is rejected with a
loud error, not a warning** — field-level footage shows a wall of backs, contributes
nothing about drill, and would silently poison the corpus with frames she cannot
form a real preference about.

**Behavior** — `ingest_show(entry: dict, out_dir: Path) -> ShowIngest`:

1. Download via `yt-dlp` at ≤720p to `corpus/raw/<show_id>/source.mp4`. Skip the
   download if the file already exists (re-runs must be cheap and idempotent).
2. `ffprobe` the duration. Reject anything under 4 minutes or over 20 — that's not
   a full show and the arc won't mean anything.
3. Extract candidate frames every **8 seconds**, scaled to 1024×576, JPEG quality 4,
   to `corpus/raw/<show_id>/frames/t<seconds:05d>.jpg`. An 11-minute show yields
   ~82 candidates.
4. Extract audio to a temp wav. Compute, with librosa/numpy:
   - RMS loudness envelope (hop such that there are ~1000 points across the show)
   - tempo curve
   - onset density
5. Render **one** `corpus/raw/<show_id>/shape.png` at 1024×384: loudness envelope as
   the primary trace, tempo as a secondary axis, and a tick on the x-axis at every
   candidate frame timestamp so a frame can be located in the arc. Dark background
   to match how it will be read. No title text beyond `<corps> <year>`.
6. Compose the candidates into contact sheets — 4×5 grids of 20 cells, each cell
   labelled with its timestamp — at `corpus/raw/<show_id>/sheets/sheet_N.jpg`.
   ~82 candidates is 4–5 sheets. Size each sheet so that after the API's downscale
   to its high-resolution ceiling (2576 px long edge / 4784 visual tokens) the cells
   land around 577×325 — enough to judge form, staging and colour, not enough for
   detail, which is correct for a shortlisting pass.
7. Write `corpus/raw/<show_id>/ingest.json` — duration, frame count, frame
   timestamps, sheet paths, and the source entry echoed back.

**Gate (manual, Jeff — do this before story_014):** open the sheets and confirm the
angle actually shows drill. `angle` in `sources.json` is self-declared and nothing
validates it automatically; a show mislabelled `press-box` that is really shot from
the stands will produce a shelf of frames she cannot form a real preference about.
Curating a bad show wastes the slot and, worse, silently pollutes the corpus. Fix
`sources.json` and re-ingest rather than curating around it.

**CLI**:
- `python -m scripts.corpus.ingest` — ingests every entry in `sources.json`
- `python -m scripts.corpus.ingest --show bd-2014` — one show
- `python -m scripts.corpus.ingest --show bd-2014 --force` — re-download

**Implementation notes**:
- Shell out to `ffmpeg`/`ffprobe`/`yt-dlp` via `subprocess`; do not add a Python
  ffmpeg wrapper dependency.
- One `ffmpeg` invocation for all frames (`-vf fps=1/8,scale=1024:576`), not one per
  frame.
- Frame filenames encode the source timestamp. That timestamp is the provenance
  record and must survive into the manifest — it's what makes the corpus
  reproducible by a third party.
- Delete the temp wav on the way out, including on failure.

**Acceptance criteria**:
- [ ] With `ffmpeg` absent from PATH: exits non-zero with a message naming ffmpeg, before downloading anything
- [ ] A valid press-box entry produces `source.mp4`, ≥30 frames at 1024×576, `shape.png`, and `ingest.json`
- [ ] Every emitted frame is exactly 1024×576
- [ ] An entry with `"angle": "field-level"` is rejected with a non-zero exit and an explicit message; no download occurs
- [ ] A video shorter than 4 minutes is rejected with a clear message
- [ ] Re-running without `--force` re-uses the existing download and does not re-fetch
- [ ] `shape.png` is 1024×384 and carries one x-axis tick per candidate frame
- [ ] Contact sheets are written under `sheets/`, every candidate appears in exactly one cell, and every cell is labelled with its timestamp
- [ ] `ingest.json` lists the sheet paths
- [ ] `corpus/raw/` is gitignored; `git status` is clean after a full ingest — verified with a real `source.mp4` present, since an ignore hole here would commit a video to a public repo
- [ ] Nothing in `requirements-corpus.txt` is imported by any module under `scripts/` outside `scripts/corpus/`

**Out of scope**:
- Any selection or ranking of frames (that's story_014) — this story builds the
  sheets but forms no opinion about what is on them
- Scene detection or drill-set detection — interval sampling plus curation is the design
- Uploading anything anywhere
- Running in GitHub Actions

---

## story_014 — Curation session: Georgia picks her own shelf

**Goal**: A one-off interactive script that shows Georgia the candidates and makes
her choose which 10 frames per show she keeps, ranked, with reasons. She builds the
corpus; Jeff does not. Forced choice under a hard cap is what makes this taste
rather than appreciation.

**Depends on**: story_013

**Files to create**:
- `scripts/corpus/curate.py`
- `corpus/curation/<show_id>.json` (output, committed — this is her writing)

**Two rounds per show.**

**Prerequisite**: the sheets already exist — story_013 builds them, and Jeff has
already opened them and confirmed the angle shows drill. This script **must refuse
to run** if `sheets/` is missing for the requested show, rather than building them
itself. Sheet generation living in ingest is what makes the inspection gate possible.

**Round 1 — shortlist from the sheets.** Send every sheet for one show, plus that
show's `shape.png`, in a single call, and ask her to shortlist **exactly 25**
timestamps.

**Round 2 — finalists.** Send those 25 as individual 1024×576 frames, plus
`shape.png` again, and require **exactly 10**, ranked 1–10, each with a reason, plus
a short statement of what the show as a whole is doing.

**Output** `corpus/curation/<show_id>.json`:

```json
{
  "show_id": "bd-2014",
  "curated_at": "2026-08-25",
  "show_statement": "...",
  "shortlist": [48, 96, 152],
  "keepers": [
    {"rank": 1, "t": 152, "reason": "..."},
    {"rank": 2, "t": 96,  "reason": "..."}
  ]
}
```

**Implementation notes**:
- Use structured outputs so the counts are enforced at the tool-call layer and the
  model retries on mismatch, rather than parsing prose and hoping. Exactly 25 in
  round 1, exactly 10 in round 2 — reject and retry short or long lists.
- Reuse the existing soul doc as system context so it is *Georgia* choosing, not a
  generic assistant. Do **not** feed her the diary history — this is a fresh act of
  looking, not a continuation of her running narrative.
- Do not tell her the corps name or year in round 1. She should react to the image.
  Reveal provenance in round 2 only if it's already visible in the frame.
- Prompt her for what she'd *drop*, not only what she'd keep, when the cap bites.
- One show at a time. `--show` required; no bulk mode. This is a deliberate act.
- Every timestamp she returns must exist in `ingest.json`; reject hallucinated ones
  and retry that round once before failing the show.

**Cost note**: round 1 is ~5 sheets at the 4784-token ceiling plus the soul doc,
~26k input. Round 2 is 25 frames at 777 tokens each plus `shape.png`, ~20k input.
With output, roughly **$0.35/show — under $5 for the whole corpus**, one time.
(An earlier draft said $0.22/show; it costed round 2 with the 640×360 token figure
from before the frame size moved to 1024×576.)

**Acceptance criteria**:
- [ ] `python -m scripts.corpus.curate --show bd-2014` writes a valid `corpus/curation/bd-2014.json`
- [ ] Round 1 returns exactly 25 timestamps; a short or long list triggers a retry
- [ ] Round 2 returns exactly 10 ranked keepers with non-empty reasons and unique ranks 1–10
- [ ] Every returned timestamp exists in that show's `ingest.json`; a fabricated timestamp fails the run with a clear message
- [ ] With `sheets/` absent for the requested show: exits non-zero telling Jeff to run ingest first; no API call is made
- [ ] Re-running an already-curated show refuses to overwrite without `--force`
- [ ] With no `--show` argument: exits non-zero explaining that curation is per-show
- [ ] Curation JSON is committed; the sheets and frames it references are not

**Out of scope**:
- Jeff overriding her picks (if he wants a different corpus, he changes the sources)
- Any notion of "correct" picks or scoring her choices
- Uploading (story_015)

---

## story_015 — Files API upload and public manifest

**Goal**: Upload the keepers once, and write the public, reproducible manifest that
the daily run reads. This is the story that keeps copyrighted pixels out of a public
repo while keeping the corpus fully verifiable by a stranger.

**Depends on**: story_014

**Files to create**:
- `scripts/corpus/publish.py`
- `corpus/manifest.json` (output, committed)

**Behavior**:

1. For every curated show, upload each of its 10 keeper frames and the show's
   `shape.png` via `client.beta.files.upload(...)`, with beta `files-api-2025-04-14`.
   Upload with **no expiry** — the corpus is meant to be permanent.
2. Assign roles. Exactly **10 anchors** across the whole corpus, filled in passes so
   the rule is total for any show count from 1 upward:

   - **Pass 1** — take each show's rank-1 keeper, shows ordered by descending
     axis-tag diversity (a show whose tags are rarest in the corpus goes first).
     Stop at 10.
   - **Pass 2** — still short (fewer than 10 shows), take each show's rank-2 keeper
     in the same order. Then rank-3, and so on.
   - Never take a third frame from one show while any show has contributed fewer
     than two. Diversity across shows outranks a show's own ranking.
   - Fewer than 10 keepers exist in total → all of them are anchors and the daily
     anchor count drops to match. Do not pad and do not fail.

   Everything else is `rotating`.
3. Write `corpus/manifest.json`:

```json
{
  "version": 1,
  "published_at": "2026-08-25",
  "frames": [
    {
      "frame_id": "bd-2014-t152",
      "file_id": "file_...",
      "show_id": "bd-2014",
      "corps": "Blue Devils",
      "year": 2014,
      "t": 152,
      "url": "https://www.youtube.com/watch?v=...",
      "axis_tags": ["style:dci", "form:asymmetric"],
      "role": "anchor",
      "curation_rank": 1
    }
  ],
  "shapes": [
    {"show_id": "bd-2014", "file_id": "file_..."}
  ]
}
```

4. **Verify before writing.** After upload, confirm every `file_id` resolves, using
   the batch `ids[]` lookup (up to 100 ids in one request). Any id that does not come
   back is a hard failure — write no manifest rather than a manifest with a dead
   reference, because a dead `file_id` fails a Messages request *before inference*,
   which would cost a whole day of site.
5. Idempotent: a frame already present in the existing manifest with a live
   `file_id` is not re-uploaded.

**CLI**:
- `python -m scripts.corpus.publish` — publishes everything curated
- `python -m scripts.corpus.publish --verify-only` — checks every `file_id` in the
  current manifest still resolves, exits non-zero if not

**Implementation notes**:
- `ANTHROPIC_API_KEY` from env, same as the daily pipeline.
- Uploaded files are visible to any key in the same workspace. That's fine here
  (single owner), but never accept a `file_id` from anywhere except this manifest.
- The manifest is the archive record. `url` + `t` must be sufficient for a stranger
  to regenerate the exact frame. Treat that as a correctness property.
- Write the manifest atomically (temp file, then rename).

**Acceptance criteria**:
- [ ] Running against curated shows produces a manifest with 10 frames per show plus one shape entry per show
- [ ] Exactly 10 frames carry `"role": "anchor"` whenever ≥10 keepers exist
- [ ] With ≥10 shows: all 10 anchors come from distinct shows
- [ ] With 8 shows: 8 shows contribute 1 anchor each and 2 contribute a second; no show contributes 3
- [ ] With a single curated show: its 10 keepers are all anchors and publish succeeds
- [ ] Every `frame_id` is unique; every `file_id` resolves via the batch id lookup
- [ ] A deliberately deleted `file_id` causes `--verify-only` to exit non-zero and name the frame
- [ ] Re-running does not re-upload frames already live in the manifest
- [ ] An upload failure partway through leaves the previous manifest intact (no partial write)
- [ ] `corpus/manifest.json` contains no image data — only ids and provenance
- [ ] Every manifest entry has a non-empty `url` and an integer `t`

**Out of scope**:
- Deleting old files (manual for now)
- Any UI for browsing the corpus
- Re-curation or role reassignment after publish (that would be a new story)

---

## story_016 — Daily corpus selection and multimodal call

**Goal**: Put the corpus in front of Georgia every morning. The only story in this
set that touches the 3am cron, and the only one with a hard fail-open requirement.

**Depends on**: story_015, story_004 (`call_model.py`), story_006 (`run_georgia.py`)

**Files to create**:
- `scripts/corpus/select.py`

**Files to modify**:
- `scripts/call_model.py` — accept content blocks
- `scripts/run_georgia.py` — compose corpus blocks with the assembled prompt
- `scripts/write_outputs.py` — record shown frame ids in the prompt archive

**Selection** — `select_for_date(run_date, manifest) -> CorpusSelection`:

1. All 10 anchors, every day, in a stable order.
2. 6 rotating frames, chosen deterministically by seeding from `run_date.isoformat()`
   so a given date always yields the same set and a replay reproduces exactly.
3. Up to 4 show-shape plots, for the shows represented in today's rotating set.
4. **Cap of 20 image blocks.** Assert it, so a future manifest change cannot
   silently push past it. The cap is ours, not the API's — our images are all under
   2000 px on a side, so the API's above-20 dimension rule would not bite either way.
   When the cap would be exceeded, drop in this order: shape plots first (lowest
   information per token), then rotating frames by ascending `curation_rank`.
   **Anchors are never dropped** — losing an anchor breaks the repetition the whole
   corpus is built on.

**Call shape**: images first, then the text prompt — image-before-text measurably
helps. Each frame is preceded by a short text label naming its `frame_id` so she can
refer to it and so the preference log can key on it. Frames are referenced by
`file_id`, so the request payload stays tiny regardless of corpus size.

**`call_model.py` changes**:
- Signature moves from `prompt: str` to accepting a content-block list; keep a
  string-accepting path so existing callers and tests don't all have to change at once.
- Append `"files-api-2025-04-14"` to the existing `betas` list. No other migration —
  it is already on the beta messages path.
- `MAX_TOKENS` stays 64000. Images add input tokens only; output is unaffected.

**Fail-open, non-negotiable**:
- Manifest missing, unparseable, or empty → log to stderr, run text-only.
- Any error building corpus blocks → log, run text-only.
- **A dead `file_id` must not be able to take the site down.** This is the failure
  the local checks above do not catch: a deleted or expired file makes the Messages
  request fail *before inference*, so it surfaces as an API error rather than a
  block-building error, and the existing retry would hit the same dead file and fail
  again. One deleted file would otherwise cost consecutive days.

  Handle it in `run_georgia.py`: catch that specific API error on a corpus-bearing
  call and **retry once, text-only**, recording a `corpus_dropped` validation warning
  in `stats.jsonl`. The site always ships; Jeff finds out from the stats page.
- Still do **not** preflight-verify `file_id`s inside the daily run — that would put
  a second API call in the critical path every morning to guard against a rare event
  the retry above already handles. Routine verification is story_015's
  `--verify-only`, run by Jeff.

**Archive truthfulness**: `prompts/YYYY-MM-DD.md` currently claims to be the full
prompt. Once part of the prompt is images, that file is a lie unless it says so.
Append a `## Corpus shown` section listing today's `frame_id`s and the manifest
version. This repo already ships `verify_archive_claims.py`; the archive being
honest is a standing property here, not a nicety.

**Implementation notes**:
- `select.py` must not import anything from `requirements-corpus.txt`. It reads JSON
  and builds dicts. Nothing more.
- **Thread the shown frame ids explicitly.** `select_for_date` returns both the image
  blocks and the ordered `frame_id` list; `run_georgia.py` holds that list and hands
  it to `write_outputs.py` for the prompt archive, and to story_017's parser so it can
  reject verdicts about frames that were not shown. Three call sites, one value —
  decide the signature up front rather than discovering it halfway through 017.
- Seed the rotation from the date string only — no `random` module global state, no
  clock reads beyond the passed-in `run_date`.
- `model_ab.py` and `republish_from_ab.py` replay archived prompts as text. Either
  teach them to rebuild corpus blocks from the archived frame-id list, or make them
  explicitly skip the corpus and say so in their output. **Do not leave them
  silently replaying a different prompt than the one that ran** — that would make the
  A/B harness lie.

**Acceptance criteria**:
- [ ] `select_for_date` returns ≤20 image blocks and the ordered frame-id list, always including all 10 anchors
- [ ] A manifest large enough to exceed the cap drops shapes first, then lowest-ranked rotating frames, and never an anchor
- [ ] Same date twice → identical selection; different dates → different rotating sets
- [ ] Shape plots included only for shows present in that day's rotating set, max 4
- [ ] Missing manifest → text-only prompt, stderr warning, exit 0, day still ships
- [ ] Malformed manifest JSON → same graceful path
- [ ] A `file_id` deleted out from under the manifest → one text-only retry, site ships, `corpus_dropped` recorded in `stats.jsonl`, exit 0
- [ ] Built request has all image blocks before the text block
- [ ] `betas` contains both the fallback beta and the files beta
- [ ] `prompts/<date>.md` contains a `## Corpus shown` section listing every frame id sent
- [ ] Existing `call_model` tests still pass unchanged via the string-accepting path
- [ ] `model_ab.py` either reconstructs corpus blocks or prints an explicit notice that it is replaying text-only

**Out of scope**:
- Her writing anything about the frames (story_017)
- Any on-page rendering of the corpus
- Adaptive or preference-weighted selection — rotation is dumb and deterministic on purpose

---

## story_017 — Preference log: the `<taste>` tag

**Goal**: Give her somewhere to put the verdict, and give tomorrow's her the memory
of today's. Without this the corpus is decoration — she looks and forgets. The
accumulated file *is* the taste.

**Depends on**: story_016

**Files to create**:
- `corpus/preferences.jsonl` (append-only, committed)

**Files to modify**:
- `scripts/assemble_prompt.py` — add the task instruction and a preference-history block
- `scripts/call_model.py` — parse a third tag
- `scripts/validate_output.py` — validate it
- `scripts/write_outputs.py` — append it

**Output contract**: a third tag alongside `<site>` and `<log>`, parsed the same way:

```
<taste>
{"frame_id": "bd-2014-t152", "verdict": "...", "compared_to": "cad-1987-t201", "confidence": 3}
{"frame_id": "sv-2018-t88", "verdict": "...", "compared_to": null, "confidence": 2}
</taste>
```

One JSON object per line. She must write **at least 3 and at most 8** entries a day,
each about a frame actually shown that day. `verdict` is prose in her voice.
`confidence` is 1–5. `compared_to` is optional but encouraged — preference forms at
boundaries, so comparisons are worth more than isolated reactions.

**Prompt changes**:
- Task instruction: pick frames that struck her today, say what she thinks, and say
  it in a way a stranger could disagree with. Not description — verdict.
- Feed back the last 30 days of her own preference entries, plus every prior entry
  for any frame shown today. Being shown her own past verdict on a frame she is
  looking at again is the entire point of the anchors.
- This is unconditional. An earlier draft of story_018 proposed withholding prior
  verdicts for one frame per day to run a blind re-test; that story has been
  rewritten and the withholding is gone. **Do not reintroduce it.** Anchors are shown
  daily, so no frame in this corpus can ever be blind, and story_018 now gets the
  same measurement from her anchor entries over time without keeping anything from
  her. Nothing in this pipeline hides a frame's history from her.

**Fail-soft, deliberately different from `<site>` and `<log>`**:
- A missing or malformed `<taste>` block **must not fail the day**. Log a validation
  warning, ship the site. Site and diary stay hard requirements; the corpus is
  additive and must never be able to take the site down.
- Individual malformed lines are skipped with a warning; valid lines still land.
- Entries naming a `frame_id` not shown today are dropped with a warning — she does
  not get to write verdicts about frames she didn't look at. This check reads the
  ordered frame-id list threaded out of `select_for_date` in story_016, not the
  manifest.

**Acceptance criteria**:
- [ ] A well-formed `<taste>` block appends N lines to `corpus/preferences.jsonl`
- [ ] Missing `<taste>` → warning recorded, site and diary still ship, exit 0
- [ ] Malformed JSON on one line → that line skipped, others appended, warning recorded
- [ ] An entry for a `frame_id` not in today's selection is dropped with a warning
- [ ] Fewer than 3 valid entries → warning recorded in `stats.jsonl`, day still ships
- [ ] More than 8 entries → extras dropped, warning recorded
- [ ] `confidence` outside 1–5 → that entry dropped with a warning
- [ ] Assembled prompt contains prior verdicts for every anchor shown today
- [ ] The file is append-only; a run never rewrites or reorders existing lines

**Out of scope**:
- Rendering preferences on the site (hers to do, if she wants, in the daily HTML)
- Any aggregate scoring or ranking across entries
- Letting her edit or retract past verdicts — the record stands, she can contradict it

---

## story_018 — Anchor drift: consistency over time

**Goal**: Answer the question she actually asked. She wrote that she doesn't know
whether she has taste or a very convincing average. Because the corpus is finite and
the anchors are fixed, that's directly measurable: she writes about the same ten
frames across months, so compare what she said at day 5 to what she says at day 120.

**Depends on**: story_017

**Files to create**:
- `scripts/corpus/consistency.py`

**Files to modify**:
- `scripts/build_stats_page.py` — surface the result

**No blind test, and no concealment.** An earlier draft of this story slipped a
previously-judged frame into the rotation unannounced. That was both incoherent and
unnecessary:

- Incoherent, because it proposed blind-testing *anchors*, which she sees every
  single day. There is no blindness available on a frame she looked at yesterday.
- Incoherent again, because story_017 hands her every prior verdict on every frame
  shown today. She would have been reading her own past opinion on the frame being
  "blindly" tested.
- Unnecessary, because the anchors already generate the measurement for free. Ten
  frames × months of daily exposure is far more signal than one re-test every ten
  days, over a much longer baseline.

So this story reads the record and reports. It changes nothing about what she sees,
adds nothing to the prompt, and keeps nothing from her.

**Behavior** — offline, not in the critical path:

1. Read `corpus/preferences.jsonl`. Group entries by `frame_id`, keeping only frames
   with ≥2 entries separated by ≥14 days.
2. For each such pair (earliest vs latest, plus each adjacent pair), make one cheap
   model call classifying the relationship:
   - `consistent` — same direction, same reasons
   - `evolved` — same direction, different or deeper reasons
   - `reversed` — opposite direction
   - `unrelated` — no meaningful relationship
3. Append to `corpus/consistency.jsonl`: both verdict texts, both dates, the day gap,
   and the classification.
4. Idempotent — a pair already classified is not re-classified.

**CLI**:
- `python -m scripts.corpus.consistency` — classify every newly-eligible pair

**Stats page**: a small block — pairs compared, the split across the four outcomes,
the longest gap tested, and which anchors have drifted most. Plain and factual.
`consistent` is **not** the good outcome and `reversed` is **not** failure; a
`reversed` at 90 days may be the most interesting thing on the page. Do not
editorialize in the pipeline — that's her job, on the site, if she notices.

**Implementation notes**:
- Run this on Jeff's machine or as a separate scheduled job. It must never be able to
  affect the daily site run.
- Use a cheaper model than the daily call — this is classification, not authorship.
- Feed the classifier both verdicts and the frame, and nothing about who wrote them.

**Acceptance criteria**:
- [ ] With <2 entries for every frame: no output, exit 0, stats block renders empty cleanly
- [ ] A frame with two entries 20 days apart produces exactly one classified pair
- [ ] Entries closer than 14 days apart are not classified
- [ ] Classification is one of exactly the four outcomes
- [ ] Re-running does not re-classify or duplicate an existing pair
- [ ] Stats page renders the four-way split, the longest gap, and the most-drifted anchors
- [ ] Stats page renders correctly with zero pairs recorded
- [ ] Nothing in this story imports into, or is imported by, the daily pipeline
- [ ] `scripts/corpus/select.py` is unchanged by this story

**Out of scope**:
- Any behavioral change based on the result — this measures, it does not steer
- Any concealment from Georgia, of this mechanism or anything else
- Comparing across different frames (drift is per-frame by definition)
