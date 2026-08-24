# Corpus build — autonomous handoff

You are finishing the drum corps taste corpus for Georgia. Work through this
end to end without asking Jeff for input unless a **Stop and ask** condition
below is hit. He wants to be uninvolved until it is time to push.

## What this is

Georgia is an AI that rebuilds clarkle.com every night. She asked for "a corpus
I can have taste about" — a small, fixed set of images she sees repeatedly, so
preference can form from being moved by specific things rather than from reading
descriptions of people being moved.

She has no video input and no audio input. Still frames are the entire menu.
Drum corps survives that better than most footage because drill is composition
on a 100-yard grid, designed to be read from above as a static form.

Read these before starting, in order:

1. `stories.md` — the corpus set is **story_013 through story_018**, at the end
   of the file, with its own dependency graph and a shared "Corpus Facts" block.
   Those stories are the spec. Their acceptance criteria are the done bar.
2. `CLAUDE.md` — repo conventions. Note the split: chaos belongs to Georgia's
   output; the pipeline around her is boring and reliable.
3. `scripts/corpus/ingest.py` and `scripts/corpus/framing.py` — already built.

## Where things stand

- **story_013 is done and verified.** Four shows are ingested: `cav-2004`,
  `crown-2014`, `bahs-2021`, `mad-1995`. Jeff confirmed the field/other split
  looks right on Madison, which was the hardest case.
- `corpus/sources.json` holds all **19** shows.
- **story_014 through story_018 are unbuilt.** That is your job.
- Full suite is **348 passing**. Keep it that way.

## Hard rules

- **Never push, and never open a pull request.** Commit locally on the current
  branch and stop. Jeff pushes.
- **Never commit `corpus/raw/`.** It holds ~20GB of video and copyrighted frames.
  It is gitignored; verify `git status` is clean after any ingest.
- **Never break the daily pipeline.** A cron runs `scripts/run_georgia.py` at 3am
  against the live site. Every corpus path must fail open: if the manifest is
  missing, malformed, or a `file_id` is dead, the day ships text-only with a
  warning. Site and diary stay hard requirements; the corpus is additive and must
  never be able to take the site down.
- **Never lower a shortlist target to fit a thin candidate pool.** If a show has
  too few field frames, sample it denser via `interval_s`. Shrinking the cap
  removes exactly the pressure the cap exists to create — forced choice is the
  only thing separating taste from appreciation. This was considered and
  explicitly rejected; do not re-derive it.
- **Never delete a Files API object** except one you uploaded in a failed run and
  are cleaning up in the same session.
- **Never skip, disable, or weaken a test to get green.** If a test now fails
  because behaviour legitimately changed, change the test deliberately and say so
  in the commit message.

## The work

Do these in order. Commit after each, with a message explaining *why*, not just
what. Run the full suite before every commit.

### 1. Ingest the remaining 15 shows

```bash
python -m scripts.corpus.ingest          # skips the four already downloaded
```

Expect ~1GB and a few minutes per show. Re-runs reuse existing downloads; only
`--force` refetches. Do not use `--force`.

### 2. Verify every show's field split — you can actually look

This is the step Jeff and the previous agent could not do well, because neither
could see the frames from where they were. **You can.** Use the Read tool on the
sheet JPEGs directly:

```
corpus/raw/<show_id>/sheets/field_01.jpg
corpus/raw/<show_id>/sheets/other_01.jpg
```

For each of the 19 shows, open at least one sheet of each kind and check:

- **Is the `angle` tag honest?** If a show tagged `high` is really shot from the
  stands, fix `sources.json` and re-ingest. Nothing validates this automatically.
- **Are real formations stranded in `other`?** Cells are labelled with their
  field score, so you can read the boundary directly off the image.
- **Is the field pool big enough?** Round 1 shortlists 25; aim for **at least 60**
  field frames. Under that, add an `interval_s` override (2–30) in
  `sources.json` and re-extract. Madison is already at 3 for this reason.

`framing.FIELD_THRESHOLD` is 0.25, placed in a measured gap: junk scores
0.00–0.24, real drill 0.27–0.54. Tests pin both bands. Do not move it to fix one
show — use a per-show `interval_s` instead.

**The known risk is the modern shows.** `bloo-2024` and `man-2023` may tarp over
most of the field, which would break a turf detector outright rather than merely
mis-tune it — the symptom is a show where almost nothing clears the threshold and
ingest warns about it. If that happens, do not chase the constant. Build
`scripts/corpus/classify.py`: send each candidate to `claude-haiku-4-5` and ask
whether it shows formations across the field from an elevated angle. About 777
tokens per frame, ~$0.16 a show. Use it only for shows the heuristic actually
fails; keep the free scorer for the rest.

### 3. Build and run story_014 — curation

Georgia picks her own shelf. This is the point of the whole exercise, so read
story_014 carefully. The essentials:

- Two rounds per show: shortlist exactly **25** from the contact sheets, then
  exactly **10** ranked keepers with reasons.
- **Only the `field_*` sheets go to round 1.** The `other_*` frames are category
  noise; making her sort them wastes attention that should go to choosing.
- Use structured outputs so the counts are enforced at the tool-call layer.
- Use `georgia-soul.md` as system context so it is *Georgia* choosing, not a
  generic assistant. Do **not** feed her the diary history — this is a fresh act
  of looking.
- Do not reveal corps or year in round 1. She reacts to the image.
- Every timestamp she returns must exist in that show's `ingest.json`.

Then run it for all 19 shows. Budget roughly $0.35 a show.

### 4. Build and run story_015 — upload and manifest

Uploads keepers to the Files API and writes `corpus/manifest.json`. This is what
keeps copyrighted pixels out of a public repo while leaving the corpus fully
reproducible by a stranger from `url` + `t`.

Verify every `file_id` resolves before writing the manifest. A dead reference
fails a Messages request *before inference*, which would cost a whole day of site.

### 5. Build story_016 — daily selection

The only story that touches the cron. 10 anchors every day, 6 rotating, up to 4
shape plots, 20 images total. Images before text. Add `files-api-2025-04-14` to
the existing `betas` list in `call_model.py` — it is already on the beta path, so
this is an append, not a migration.

Two things easy to miss, both spelled out in the story: `prompts/<date>.md` must
record which frames were shown or the archive becomes a lie, and `model_ab.py`
must not silently replay a different prompt than the one that ran.

### 6. Build story_017 — the `<taste>` tag

Without this the corpus is decoration: she looks and forgets. The accumulated
preference log *is* the taste. Fail-soft — a missing or malformed `<taste>` block
must never fail the day.

### 7. Build story_018 — anchor drift

Offline analysis only. Read story_018's opening section: an earlier draft
proposed a covert blind re-test, and it was removed as both incoherent and
unnecessary. Do not reintroduce it.

## Stop and ask Jeff

Commit what you have, write a short summary, and stop if:

- Total API spend passes **$15**. Expected total is about $8.
- A show cannot be made usable — wrong angle, dead link, or a field pool still
  under 60 after a denser interval. Say which show and why; do not silently drop
  it, and do not pad the corpus to compensate.
- Any change would make the daily pipeline able to fail on a corpus problem.
- You find a genuine contradiction between two stories. One such conflict already
  shipped in this plan and was caught in review; assume another is possible.

## Done looks like

- 19 shows ingested, every split visually verified by you.
- 190 curated keepers, `corpus/curation/*.json` committed.
- `corpus/manifest.json` with 10 anchors from distinct shows, every `file_id` live.
- Stories 013–018 meeting their acceptance criteria.
- Full suite green, with new tests for everything you built.
- `git status` clean, nothing from `corpus/raw/` staged, **nothing pushed**.
- A summary for Jeff: what you built, what you decided and why, what surprised
  you, what is still uncertain, and the exact commands he should run to sanity
  check before pushing.

Write that summary to `corpus/BUILD-REPORT.md` so it survives the session.
