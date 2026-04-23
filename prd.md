# clarkle.com — Product Requirements Document

*Last updated: 2026-04-22 (rev 3)*

---

## Vision

Turn clarkle.com into a living demonstration of AI capability — a site that fully reimagines itself every day, hands-off, via an automated AI pipeline. The goal is to establish Jeff Clark as a serious AI builder and power user, drive repeat traffic through novelty, and generate professional opportunities and speaking engagements.

The key insight: this isn't just a daily redesign. The AI takes the wheel on **everything** — design, copy, tone, voice, structure. One day it's written in poetry. One day it looks like a 1995 Tripod site with animated GIFs. One day it's a dead-serious professional blog. The chaos is the point.

---

## Core Concept

- **Fully automated daily pipeline**: cron job fires → AI generates a creative brief (the prompt) → AI generates a complete HTML site from that brief → auto-deploys → archives the previous version
- **Totally hands-off**: Jeff does not touch the site. The AI runs it.
- **Fully public**: the GitHub repo, the prompts, the archive, the Action logs — all visible. Transparency is a feature, not a side effect.
- **Source of truth**: a small JSON file of inviolable facts the AI must include regardless of how wild it gets (see below). Everything else is fair game.

---

## What's Locked In

### Platform

- **Hosting**: GitHub Pages (free, static, custom domain support)
- **Automation**: TBD — GitHub Actions vs. Cowork scheduled task (see Open Questions)
- **Repo**: Public GitHub repo under account `jeffclark` — the repo itself is part of the demo

### Daily Pipeline

1. Cron fires at **3am ET** daily
2. Pipeline assembles Georgia's context packet (see Context Architecture below)
3. AI generates complete HTML site + Georgia's log entry for the day
4. New `index.html` committed to repo
5. Archive file saved to `/archive/YYYY-MM-DD.html`
6. Georgia's log entry saved to `/log/YYYY-MM-DD.md`
7. Prompt logged publicly in repo
8. GitHub Pages auto-deploys on commit

### Context Architecture

Every day the pipeline hands Georgia a layered context packet assembled from four sources:

**Layer 1 — Soul** (`georgia-soul.md`)
Fed in full every day, unchanged. Who Georgia is, what she believes, her voice, her worldview. The foundation everything else is built on.

**Layer 2 — Facts** (`facts.json`)
Jeff's name, email, LinkedIn URL, LinkedIn title, project list. Inviolable. Fed in full every day. Georgia must include these regardless of how chaotic the day gets.

**Layer 3 — History** (`/log/`)
Georgia writes a brief log entry at the end of every run — what she made, what she was going for, what she thought of it. She also self-tags each entry with an importance score (1–5). The next day's pipeline feeds her two bundles: (a) the last 14 days verbatim (vivid recency), and (b) the top 20 older entries scored by `importance × exp(-days_ago / 180)` (things she still thinks about). All history is retained; important entries persist, unimportant ones fade. Georgia builds her own continuity. She remembers what mattered. She has opinions about it.

**Layer 4 — Feedback** (`/feedback/YYYY-MM-DD.json`)
Yesterday's data: vote count, visitor numbers, and optionally a note from Jeff. Jeff leaves a note by dropping a file in an agreed location (TBD — could be a file in the repo, an iMessage, a Slack message). Georgia gets it, reacts to it, moves on. If there's no note, she notes the absence.

The assembled prompt looks roughly like this:

```
You are Georgia. [soul doc]
These are the facts about Jeff: [facts.json]
Recent history (last 14 days, vivid): [verbatim entries]
Older — things you still think about: [weighted top-20 older entries]
Here is yesterday's feedback: [votes + visitors + Jeff's note if exists]
Today is [date]. Build today's site.
Then write your log entry for today (tag it with an importance score 1-5).
```

**Georgia produces two outputs every run:** the HTML for today's site, and her diary entry for the log. Both get committed. The diary entry becomes part of next week's history. Over time, the `/log/` directory becomes a character study written by the character about itself.

### Source of Truth (inviolable facts)

These must appear on every version of the site regardless of theme. Everything else — tone, copy, structure, design — can be reimagined completely.

- Jeff's name
- Email address ([jeff@clarkle.com](mailto:jeff@clarkle.com))
- LinkedIn URL
- LinkedIn title/role
- Project list (one factual sentence per project — **see Open Questions**)

### Site Sections (stable facts, chaotic presentation)

Every version of the site should address these areas, though how they're presented is completely up to the AI:

- Who Jeff is / intro
- AI project portfolio
- Build-in-public log (reframe of existing blog — raw, honest experiment notes)
- Contact / LinkedIn

### Archive (CSS Zen Garden model)

- Each day's full HTML saved to `/archive/YYYY-MM-DD.html` — live HTML, not screenshots
- Archive index page lets visitors browse and click into any past version
- Clicking a date loads that day's actual site — not a screenshot, the real thing

### Prompt Transparency

- The prompt (or creative brief) used to generate each day's site is stored in the repo and displayed on the site
- The fact that the AI is prompting *itself* is a core part of the story — show the full chain

### Build-in-Public Log

- Fresh start — no migration of existing blog posts
- Short, scrappy posts: "tried this, it failed because Y, pivoted to Z"
- Not polished thought leadership — honest process notes
- **Publishing mechanic**: novel AI-assisted workflow TBD — current leading idea is iMessage → Claude picks it up → formats and publishes to site. Could also be Slack-based. Either way, AI is in the loop. Not at launch — future feature.

### DNS

- clarkle.com pointed to GitHub Pages
- **Must preserve**: MX records, coach.clarkle.com subdomain, all existing subdomains/records
- Approach: add GitHub Pages A records, CNAME for www, leave everything else untouched

---

## Voting / Theme Selection Mechanic

### How it works (v1 — future feature, not at launch)

Each day's site displays **3–5 AI-generated theme suggestions** for the next day (e.g. "1995 Geocities", "Corporate Dystopia", "Haiku Everything"). Visitors vote on which theme they want. When the 3am cron fires, it picks the winning theme and builds the next day's prompt around it.

This closes the loop: visitors influence the output, have a reason to come back tomorrow to see if their theme won, and feel ownership over the chaos.

- The themes themselves are AI-generated (not manually curated)
- Vote data is surfaced publicly
- **Not at launch** — needs traffic before this mechanic has any meaning

**Voting implementation**: TBD — see Open Questions.

---

## Content Guardrails

The AI is encouraged to be genuine and funny, with a twist of weird and chaotic. Hard constraints:

- No actual NSFW/explicit content
- No content that could be defamatory
- Inviolable facts (above) must always appear and be accurate
- Everything else: fair game

---

## What This Is NOT (yet)

These ideas surfaced but are not in scope for the initial build:

- "Chat with AI Jeff" (custom agent trained on Jeff's writing) — future phase
- Interactive portfolio with embedded live demos — future phase
- Voting / theme selection mechanic — future phase (needs traffic first)
- iMessage/Slack → AI → publish workflow for build-in-public log — future phase
- **News/current events awareness** — future phase. Georgia pulls a live feed (AI news, world news, sports TBD) as an optional Layer 5 in her context packet. She can use it or ignore it as she sees fit. Some days she builds the site around a headline. Some days she doesn't mention it. The choice is hers. Adds a Layer 5 (`/feeds/`) to the context architecture.

---

## Open Questions


| #   | Question                                                         | Why It Matters                                                                                                            | Status       |
| --- | ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- | ------------ |
| 1   | **Who is the DNS provider / registrar for clarkle.com?**         | Blocking — can't touch DNS without knowing where to go                                                                    | ❌ Unresolved |
| 2   | **What is currently serving clarkle.com?**                       | Need to know what we're migrating away from                                                                               | ❌ Unresolved |
| 3   | **Project inventory**                                            | Need one factual sentence per AI project for the source of truth JSON                                                     | ❌ Unresolved |
| 4   | **Automation runtime: GitHub Actions vs. Cowork scheduled task** | Affects reliability, transparency story, and how the pipeline is built                                                    | ❌ Unresolved |
| 5   | **Voting mechanism implementation**                              | Options: GitHub Issues per day, Formspree, lightweight serverless function. Needs to be static-friendly                   | ❌ Unresolved |
| 5a  | **How does Jeff leave a note for Georgia?**                      | File drop in repo? iMessage? Slack? Needs to be low-friction or Jeff won't do it                                          | ❌ Unresolved |
| 5b  | **Visitor analytics source**                                     | GitHub Pages has no built-in analytics. GoatCounter has a free API and is privacy-friendly. Needs decision before launch. | ❌ Unresolved |
| 6   | **Prompt seeding: does the AI get context on past designs?**     | Yes — last 7 log entries fed in as Layer 3. Resolved by context architecture.                                             | ✅ Resolved   |
| 7   | **GitHub account**                                               | `jeffclark` — confirmed                                                                                                   | ✅ Resolved   |
| 8   | **Cron time**                                                    | 3am ET daily                                                                                                              | ✅ Resolved   |
| 9   | **Archive format**                                               | Live HTML                                                                                                                 | ✅ Resolved   |
| 10  | **Build-in-public log: migrate or start fresh?**                 | Start fresh                                                                                                               | ✅ Resolved   |
| 11  | **Vote data influences next day's prompt?**                      | Yes — via theme voting mechanic. Not at launch.                                                                           | ✅ Resolved   |


---

## Success Metrics (qualitative for now)

- Site gets shared / screenshotted without Jeff promoting it
- Inbound LinkedIn messages referencing the site
- Speaking/consulting inquiry referencing the site
- Repeat visitors who check back to see what changed

---

*Next step: resolve Open Questions 1–3 (blocking), then begin build. Questions 4–6 can be decided during build.*