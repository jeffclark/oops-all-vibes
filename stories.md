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
