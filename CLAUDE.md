# oops-all-vibes

clarkle.com daily-regenerating AI-authored website. Georgia (Claude Sonnet 4.6) rebuilds the site from scratch every 24 hours via a GitHub Actions cron. Everything public — repo, prompts, archive, diary, stats.

## Source of truth

- **stories.md** — engineering stories in dependency order. Start at story_001 and work through. The AC checkboxes are the done bar.
- **prd.md** — product context. Read once for background; don't iterate on it mid-build.
- **georgia-soul.md** — Georgia's voice and worldview. Currently at the Cowork OS root; story_002 moves it into this folder as part of setup.

## How to work here

- Pick stories in the dependency order shown in stories.md. Meet AC, move on. Don't add polish beyond AC.
- Each story lists its files, implementation notes, AC, and out-of-scope. Stay in scope.
- If anything is ambiguous mid-implementation, ask Jeff rather than guess. This is a personal site tied to his name.
- Don't push to GitHub until story_012 (dry-run + DNS cutover). Local commits only before that.
- Don't commit secrets. All API keys live in env vars and GitHub Actions secrets.

## The split

The site is chaotic on purpose. The pipeline around it is not. Chaos belongs to Georgia (what she outputs); everything else — the orchestration, the validation, the analytics, the stats page — should be boring, reliable, and easy to reason about.

## Repo

`jeffclark/oops-all-vibes` (public). Created in story_001.
