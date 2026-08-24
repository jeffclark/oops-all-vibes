"""Offline A/B: replay archived prompts against two models and diff the output.

Reads real prompts from prompts/<date>.md and calls each model arm once per
date, writing the parsed <site> HTML and <log> diary to model-ab/<date>/ plus
a side-by-side viewer at model-ab/index.html. Each arm's output is then put
through validate_output — the same gate that decides a retry in the real run —
so the comparison covers "would this have shipped", not just how it looks.

verify_archive_claims is deliberately NOT run: it checks the page against the
archive currently on disk, which has moved on since these prompts were written,
so a replayed page would be judged against dates it couldn't have known about.

**Corpus days replay text-only, and say so.** Once story_016 started sending
frames, prompts/<date>.md became the text half of the request plus a
`## Corpus shown` list of what went with it. This tool replays that file
verbatim, so on such a day both arms see the text and neither sees the images.
Rebuilding the blocks would compare a different request than the one that ran,
which is a subtler lie than the one it fixes — so it is called out instead, on
the console and in the viewer, rather than papered over.

This is a review tool, not part of the daily pipeline. It never writes to
index.html, archive/, log/, or stats.jsonl, and run_georgia.py doesn't import
it. It does spend real API money — roughly $1.20 per date for both arms — so
it prints an estimate and asks before calling anything.

    python -m scripts.model_ab --days 5
    python -m scripts.model_ab --dates 2026-08-15,2026-08-18 --yes
    python -m scripts.model_ab --days 5 --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

from anthropic import Anthropic

# Deliberately reusing production's compiled patterns rather than re-writing
# them here: if the A/B parsed output differently from the real run, the
# comparison would be measuring the parser instead of the models.
from scripts.call_model import _LOG_RE, _SITE_RE
from scripts.validate_output import validate_output


REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "model-ab"

# write_outputs stamps this into prompts/<date>.md on any day that carried frames.
CORPUS_MARKER = "## Corpus shown"
TEXT_ONLY_NOTICE = (
    "replaying TEXT-ONLY: this day's real request also carried corpus frames as "
    "images, which are not reconstructed here"
)


def corpus_dates(prompts: dict[str, str]) -> list[str]:
    """Dates whose archived prompt records frames that this replay will not send."""
    return sorted(d for d, text in prompts.items() if CORPUS_MARKER in text)


@dataclass(frozen=True)
class Arm:
    """One side of the comparison."""

    name: str
    model: str
    max_tokens: int
    input_rate: float  # USD per million input tokens
    output_rate: float  # USD per million output tokens
    note: str


# These two arms are the before and after of the Opus 5 migration. The sonnet
# arm is the pre-migration production config (Sonnet 4.6 at 24000 max_tokens,
# no thinking parameter); the opus arm is what production runs now.
#
# Neither passes `thinking`. On Sonnet 4.6 that means no thinking at all; on
# Opus 5 it means adaptive thinking runs by default. That difference is the
# point of the comparison, so it's left alone rather than configured away.
# Thinking shares the max_tokens budget with the response text, and Opus
# tokenizes the same text to roughly 1.3x as many tokens, which is why the
# opus arm needs 64000 where the sonnet arm needed 24000.
ARMS = (
    Arm(
        name="sonnet",
        model="claude-sonnet-4-6",
        max_tokens=24000,
        input_rate=3.0,
        output_rate=15.0,
        note="current production config",
    ),
    Arm(
        name="opus",
        model="claude-opus-5",
        max_tokens=64000,
        input_rate=5.0,
        output_rate=25.0,
        note="adaptive thinking on by default; output_tokens includes unseen thinking",
    ),
)

# Rough per-date estimate used only for the pre-flight cost prompt.
ESTIMATED_USD_PER_DATE = 1.25


@dataclass
class Result:
    """Outcome of one (date, arm) call."""

    date: str
    arm: str
    model: str
    ok: bool = False
    error: str = ""
    stop_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    duration_s: float = 0.0
    html_bytes: int = 0
    diary_bytes: int = 0
    cost_usd: float = 0.0
    valid: bool = False
    validation_failures: list[str] = field(default_factory=list)
    skipped: bool = False


def select_dates(repo_root: Path, days: int, explicit: str | None) -> list[str]:
    """Pick which prompt files to replay, newest last."""
    prompts = repo_root / "prompts"
    available = sorted(p.stem for p in prompts.glob("*.md"))
    if not available:
        raise SystemExit(f"model_ab: no prompt files found in {prompts}")

    if explicit:
        wanted = [d.strip() for d in explicit.split(",") if d.strip()]
        missing = [d for d in wanted if d not in set(available)]
        if missing:
            raise SystemExit(f"model_ab: no prompt file for {', '.join(missing)}")
        return wanted

    return available[-days:]


def run_arm(
    arm: Arm,
    date: str,
    prompt: str,
    client: Anthropic,
    facts: dict | None = None,
) -> tuple[Result, str, str]:
    """Call one arm once. Returns (result, html, diary); html/diary empty on failure."""
    result = Result(date=date, arm=arm.name, model=arm.model)
    start = time.monotonic()
    try:
        with client.messages.stream(
            model=arm.model,
            max_tokens=arm.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            message = stream.get_final_message()
    except Exception as exc:  # noqa: BLE001 — one arm failing must not lose the others
        result.duration_s = time.monotonic() - start
        result.error = f"{type(exc).__name__}: {exc}"
        return result, "", ""

    result.duration_s = time.monotonic() - start
    result.stop_reason = message.stop_reason or ""
    result.input_tokens = message.usage.input_tokens
    result.output_tokens = message.usage.output_tokens
    result.cost_usd = (
        result.input_tokens * arm.input_rate + result.output_tokens * arm.output_rate
    ) / 1_000_000

    # Only text blocks — on Opus 5 the response also carries thinking blocks,
    # which are billed as output but come back with empty text.
    raw = "".join(b.text for b in message.content if b.type == "text")

    if result.stop_reason == "refusal":
        result.error = "stop_reason=refusal (safety classifier declined)"
        return result, "", ""

    site_match = _SITE_RE.search(raw)
    log_match = _LOG_RE.search(raw)
    html = site_match.group(1).strip() if site_match else ""
    diary = log_match.group(1).strip() if log_match else ""

    missing = [
        tag for tag, val in (("<site>", html), ("<log>", diary)) if not val
    ]
    if missing:
        truncated = " (hit max_tokens — output was cut off)" if result.stop_reason == "max_tokens" else ""
        result.error = f"missing or empty {', '.join(missing)}{truncated}"
        return result, html, diary

    result.ok = True
    result.html_bytes = len(html.encode())
    result.diary_bytes = len(diary.encode())

    # The same gate run_georgia.py uses to decide whether a day ships or retries.
    result.valid, result.validation_failures = validate_output(
        html, diary, facts or {}, date
    )
    return result, html, diary


def write_arm_output(out_dir: Path, arm_name: str, html: str, diary: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    if html:
        (out_dir / f"{arm_name}.html").write_text(html)
    if diary:
        (out_dir / f"{arm_name}.diary.md").write_text(diary)


def build_viewer(
    results: list[Result],
    dates: list[str],
    output_dir: Path,
    text_only_dates: list[str] | None = None,
) -> None:
    """Write model-ab/index.html — side-by-side iframes plus a numbers table."""
    by_key = {(r.date, r.arm): r for r in results}
    text_only = set(text_only_dates or ())
    sections = []

    for date in dates:
        panes = []
        for arm in ARMS:
            r = by_key.get((date, arm.name))
            page = output_dir / date / f"{arm.name}.html"
            if r and r.ok and page.exists():
                body = (
                    f'<iframe src="{date}/{arm.name}.html" title="{arm.name} {date}"></iframe>'
                    f'<p class="meta">'
                    f"{r.input_tokens:,} in / {r.output_tokens:,} out &middot; "
                    f"${r.cost_usd:.2f} &middot; {r.duration_s:.0f}s &middot; "
                    f'<a href="{date}/{arm.name}.html" target="_blank">full page &#8599;</a> &middot; '
                    f'<a href="{date}/{arm.name}.diary.md" target="_blank">diary</a></p>'
                    + (
                        '<p class="valid">passes validation</p>'
                        if r.valid
                        else '<p class="invalid"><strong>would have triggered a retry:</strong><br>'
                        + "<br>".join(escape(f) for f in r.validation_failures)
                        + "</p>"
                    )
                )
            else:
                reason = escape(r.error) if r and r.error else "not run"
                body = f'<div class="failed"><strong>No output</strong><br>{reason}</div>'
            panes.append(f'<div class="pane"><h3>{arm.name}<span>{arm.model}</span></h3>{body}</div>')
        note = (
            f'<p class="invalid"><strong>Text-only replay.</strong> {escape(TEXT_ONLY_NOTICE)}, '
            "so neither arm below saw what Georgia saw.</p>"
            if date in text_only
            else ""
        )
        sections.append(
            f'<section><h2>{date}</h2>{note}<div class="panes">{"".join(panes)}</div></section>'
        )

    rows = []
    for date in dates:
        for arm in ARMS:
            r = by_key.get((date, arm.name))
            if not r:
                continue
            status = "ok" if r.ok else escape(r.error or "failed")
            rows.append(
                f"<tr><td>{date}</td><td>{r.arm}</td>"
                f"<td>{r.input_tokens:,}</td><td>{r.output_tokens:,}</td>"
                f"<td>${r.cost_usd:.2f}</td><td>{r.duration_s:.0f}s</td>"
                f"<td>{r.html_bytes:,}</td><td>{escape(r.stop_reason)}</td>"
                f"<td>{'pass' if r.valid else 'FAIL' if r.ok else '&mdash;'}</td>"
                f"<td>{status}</td></tr>"
            )

    totals = []
    for arm in ARMS:
        arm_results = [r for r in results if r.arm == arm.name]
        cost = sum(r.cost_usd for r in arm_results)
        ok = sum(1 for r in arm_results if r.ok)
        shipped = sum(1 for r in arm_results if r.valid)
        totals.append(
            f"<li><strong>{arm.name}</strong> ({arm.model}): "
            f"${cost:.2f} across {len(arm_results)} run(s), {ok} parsed, "
            f"{shipped} would have shipped &mdash; {arm.note}</li>"
        )

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Georgia model A/B — sonnet 4.6 vs opus 5</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 15px/1.5 system-ui, sans-serif; margin: 0; padding: 2rem; max-width: 1800px; }}
  h1 {{ margin: 0 0 .25rem; }}
  .lede {{ color: #666; margin: 0 0 2rem; max-width: 60ch; }}
  section {{ margin-bottom: 3rem; }}
  h2 {{ border-bottom: 2px solid currentColor; padding-bottom: .3rem; }}
  .panes {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; }}
  @media (max-width: 1100px) {{ .panes {{ grid-template-columns: 1fr; }} }}
  .pane h3 {{ margin: 0 0 .5rem; display: flex; justify-content: space-between; align-items: baseline; }}
  .pane h3 span {{ font-weight: 400; font-size: .8rem; color: #666; }}
  iframe {{ width: 100%; height: 820px; border: 1px solid #999; background: #fff; }}
  .meta {{ font-size: .8rem; color: #666; margin: .4rem 0 0; }}
  .failed {{ border: 1px dashed #b00; padding: 2rem; color: #b00; text-align: center; }}
  .valid {{ font-size: .8rem; color: #060; margin: .3rem 0 0; }}
  .invalid {{ font-size: .8rem; color: #b00; margin: .3rem 0 0; }}
  table {{ border-collapse: collapse; width: 100%; font-size: .85rem; }}
  th, td {{ border: 1px solid #ccc; padding: .35rem .6rem; text-align: left; }}
  th {{ background: rgba(128,128,128,.15); }}
  ul {{ max-width: 80ch; }}
</style>
</head>
<body>
<h1>Georgia model A/B</h1>
<p class="lede">Each row replays a real archived prompt from <code>prompts/</code> through both
models. Same prompt, same day, one fresh sample each &mdash; the archived site for these dates was
its own separate sample, so don't read small differences as signal.</p>
<h2>Cost</h2>
<ul>{"".join(totals)}</ul>
<p class="lede">Opus <code>output_tokens</code> includes adaptive thinking, which is billed but
never returned &mdash; that's most of the gap in output cost. If the panes below are blank, you
opened this over <code>file://</code>; Chrome blocks nested local frames. Serve the folder instead
(<code>python3 -m http.server</code>) or use the &ldquo;full page&rdquo; links.</p>
{"".join(sections)}
<h2>Numbers</h2>
<p class="lede">&ldquo;Validation&rdquo; is <code>validate_output</code>, the same gate that decides a
retry in the real run. <code>verify_archive_claims</code> is not run here &mdash; it judges a page
against the archive as it stands today, which these replayed prompts predate.</p>
<table>
<tr><th>date</th><th>arm</th><th>input tok</th><th>output tok</th><th>cost</th><th>time</th><th>html bytes</th><th>stop reason</th><th>validation</th><th>status</th></tr>
{"".join(rows)}
</table>
</body>
</html>
"""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "index.html").write_text(html)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--days", type=int, default=5, help="how many recent dates to replay (default 5)")
    parser.add_argument("--dates", help="comma-separated dates to replay instead of --days")
    parser.add_argument("--workers", type=int, default=4, help="concurrent API calls (default 4)")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR, help="output directory")
    parser.add_argument("--force", action="store_true", help="re-run dates that already have output")
    parser.add_argument("--dry-run", action="store_true", help="show the plan without calling the API")
    parser.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    args = parser.parse_args(argv)

    dates = select_dates(REPO_ROOT, args.days, args.dates)
    # expanduser so a quoted "~/tmp/model-ab" works, not just a shell-expanded one
    output_dir = args.output.expanduser()

    jobs: list[tuple[str, Arm]] = []
    for date in dates:
        for arm in ARMS:
            if not args.force and (output_dir / date / f"{arm.name}.html").exists():
                print(f"model_ab: skipping {date}/{arm.name} (already exists; --force to redo)")
                continue
            jobs.append((date, arm))

    print(f"model_ab: {len(dates)} date(s), {len(jobs)} call(s) to make")
    for date in dates:
        prompt_bytes = (REPO_ROOT / "prompts" / f"{date}.md").stat().st_size
        print(f"  {date}  prompt {prompt_bytes:,} bytes")

    if not jobs:
        print("model_ab: nothing to do")
        return 0

    estimate = ESTIMATED_USD_PER_DATE * len(jobs) / len(ARMS)
    print(f"model_ab: rough cost estimate ${estimate:.2f}")

    if args.dry_run:
        print("model_ab: dry run, stopping before any API call")
        return 0

    if not args.yes:
        answer = input("Proceed and spend real API credit? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("model_ab: aborted")
            return 1

    client = Anthropic()
    prompts = {d: (REPO_ROOT / "prompts" / f"{d}.md").read_text() for d in dates}
    facts = json.loads((REPO_ROOT / "facts.json").read_text())

    with_corpus = corpus_dates(prompts)
    if with_corpus:
        print(f"model_ab: NOTE — {TEXT_ONLY_NOTICE}.")
        for d in with_corpus:
            print(f"model_ab:   {d} showed corpus frames on the day; replaying text-only")

    def execute(job: tuple[str, Arm]) -> Result:
        date, arm = job
        print(f"model_ab: calling {arm.model} for {date}...", flush=True)
        result, html, diary = run_arm(arm, date, prompts[date], client, facts)
        if html or diary:
            write_arm_output(output_dir / date, arm.name, html, diary)
        if not result.ok:
            status = f"FAILED — {result.error}"
        elif not result.valid:
            status = f"parsed but INVALID — {'; '.join(result.validation_failures)}"
        else:
            status = "ok"
        print(
            f"model_ab: {date}/{arm.name} {status} "
            f"({result.output_tokens:,} out, ${result.cost_usd:.2f}, {result.duration_s:.0f}s)",
            flush=True,
        )
        return result

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        results = list(pool.map(execute, jobs))

    # Fold in any arms skipped this run so the viewer still shows their output,
    # reusing the numbers the earlier run already recorded for them.
    previous: dict[tuple[str, str], dict] = {}
    prior_path = output_dir / "results.json"
    if prior_path.exists():
        try:
            previous = {
                (r["date"], r["arm"]): r for r in json.loads(prior_path.read_text())
            }
        except (ValueError, KeyError, TypeError):
            pass  # a corrupt prior file just means no numbers to carry forward

    for date in dates:
        for arm in ARMS:
            if any(r.date == date and r.arm == arm.name for r in results):
                continue
            page = output_dir / date / f"{arm.name}.html"
            if not page.exists():
                continue
            # Carry the earlier run's verdict forward. Defaulting `valid` to
            # False here would report a skipped-but-fine day as a failure.
            prior = previous.get((date, arm.name), {})
            results.append(
                Result(
                    date=date,
                    arm=arm.name,
                    model=arm.model,
                    ok=True,
                    skipped=True,
                    html_bytes=page.stat().st_size,
                    valid=prior.get("valid", True),
                    validation_failures=prior.get("validation_failures", []),
                    input_tokens=prior.get("input_tokens", 0),
                    output_tokens=prior.get("output_tokens", 0),
                    cost_usd=prior.get("cost_usd", 0.0),
                    stop_reason=prior.get("stop_reason", ""),
                )
            )

    results.sort(key=lambda r: (r.date, r.arm))
    (output_dir / "results.json").write_text(
        json.dumps([r.__dict__ for r in results], indent=2) + "\n"
    )
    build_viewer(results, dates, output_dir, text_only_dates=with_corpus)

    failures = [r for r in results if not r.ok]
    invalid = [r for r in results if r.ok and not r.valid]
    total = sum(r.cost_usd for r in results)
    print(f"\nmodel_ab: done. ${total:.2f} spent, {len(failures)} failure(s), "
          f"{len(invalid)} parsed-but-invalid.")
    for arm in ARMS:
        arm_results = [r for r in results if r.arm == arm.name and not r.skipped]
        if arm_results:
            passed = sum(1 for r in arm_results if r.valid)
            print(f"model_ab:   {arm.name}: {passed}/{len(arm_results)} would have shipped")
    print(f"\nmodel_ab: review with —")
    print(f"    cd {output_dir} && python3 -m http.server 8000")
    print("    open http://localhost:8000/")
    print("(Chrome blocks file:// iframes, so serve the folder rather than opening index.html directly.)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
