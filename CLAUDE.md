# oops-all-vibes

clarkle.com daily-regenerating AI-authored website. Georgia (Claude Opus 5) rebuilds the site from scratch every 24 hours via a GitHub Actions cron. Everything public — repo, prompts, archive, diary, stats.

## Source of truth

- **stories.md** — engineering stories in dependency order. Start at story_001 and work through. The AC checkboxes are the done bar.
- **prd.md** — product context. Read once for background; don't iterate on it mid-build.
- **georgia-soul.md** — Georgia's voice and worldview. Lives in this folder (story_002 moved it here).

## How to work here

- Pick stories in the dependency order shown in stories.md. Meet AC, move on. Don't add polish beyond AC.
- Each story lists its files, implementation notes, AC, and out-of-scope. Stay in scope.
- If anything is ambiguous mid-implementation, ask Jeff rather than guess. This is a personal site tied to his name.
- Don't push to GitHub until story_012 (dry-run + DNS cutover). Local commits only before that.
- Don't commit secrets. All API keys live in env vars and GitHub Actions secrets.

## Daily inputs

Georgia gets five narrow, recurring sources every build — one photograph from the FSA/OWI
negatives, Boston's 311 calls, a government surplus lot, Oklahoma State ACHA hockey, and one
Federal Register document. Narrow beats broad because narrow accumulates: the same five
sources, every day, carried forward with their own history.

- `scripts/fetch_daily_inputs.py` writes `inputs/<date>.json`. Every source is fetched
  independently and a failure is recorded rather than raised — a dead source is content, and
  Georgia is told what went quiet.
- `assemble_prompt.py` renders it as the `[inputs]` block, with counts and averages drawn
  from the last 30 days so the numbers mean something.
- `inputs/roster.json` holds the roster and the retirement countdown. Every 30 builds Georgia
  must retire one input and take on something new. She can't abstain, and she sees it coming.
- To swap a source: add a fetcher to `SOURCES` and edit the roster. That's the whole change.

## The split

The site is chaotic on purpose. The pipeline around it is not. Chaos belongs to Georgia (what she outputs); everything else — the orchestration, the validation, the analytics, the stats page — should be boring, reliable, and easy to reason about.

## Repo

`jeffclark/oops-all-vibes` (public). Created in story_001.
