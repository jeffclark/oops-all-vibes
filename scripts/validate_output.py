"""Validation of Georgia's two outputs (HTML + diary).

validate_output returns (is_valid, failure_reasons). All checks run; failures
accumulate so Georgia gets a complete picture on retry. Failure strings are
first-person hints that can be shown to her verbatim.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

import frontmatter
from bs4 import BeautifulSoup


MIN_HTML_BYTES = 1024
MAX_HTML_BYTES = 500 * 1024
MIN_BODY_TEXT_LEN = 200
MIN_DIARY_BODY_LEN = 20


def validate_output(
    html: str,
    diary: str,
    facts: dict,
    today: str,
) -> tuple[bool, list[str]]:
    failures: list[str] = []
    _check_html(html, facts, failures)
    _check_diary(diary, today, failures)
    return (not failures, failures)


# ---------- HTML ----------


def _check_html(html: str, facts: dict, failures: list[str]) -> None:
    byte_size = len(html.encode("utf-8"))
    if byte_size < MIN_HTML_BYTES:
        failures.append(
            f"Your HTML is too small ({byte_size} bytes). It must be at least "
            f"{MIN_HTML_BYTES} bytes — build a real site."
        )
    elif byte_size > MAX_HTML_BYTES:
        failures.append(
            f"Your HTML is too large ({byte_size} bytes). Keep it under "
            f"{MAX_HTML_BYTES} bytes."
        )

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:  # noqa: BLE001 — html.parser is very lenient; this is belt-and-suspenders
        failures.append(
            f"Your HTML didn't parse ({exc}). Output valid HTML from doctype "
            "through </html>."
        )
        soup = None

    if soup is not None:
        body = soup.body
        if body is None:
            failures.append(
                "Your HTML has no <body> tag. Include one — a site needs a body."
            )
        else:
            body_text = " ".join(body.get_text().split())
            if len(body_text) < MIN_BODY_TEXT_LEN:
                failures.append(
                    f"Your <body> has too little text content ({len(body_text)} chars "
                    f"after whitespace collapse). It must be at least "
                    f"{MIN_BODY_TEXT_LEN} chars — put real content on the page."
                )

    name = facts.get("name", "")
    if name and name not in html:
        failures.append(
            f"You did not include Jeff's name ({name}). This is an inviolable fact."
        )
    email = facts.get("email", "")
    if email and email not in html:
        failures.append(
            f"You did not include Jeff's email address ({email}). This is an "
            "inviolable fact."
        )
    linkedin_url = facts.get("linkedin_url", "")
    if linkedin_url and linkedin_url not in html:
        failures.append(
            f"You did not include Jeff's LinkedIn URL ({linkedin_url}). This is "
            "an inviolable fact."
        )
    for project in facts.get("projects", []) or []:
        title = project.get("title", "")
        if title and title not in html:
            failures.append(
                f"You did not include the project title '{title}'. The project "
                "list is inviolable."
            )


# ---------- Diary ----------


def _check_diary(diary: str, today: str, failures: list[str]) -> None:
    if not (diary.startswith("---\n") or diary.startswith("---\r\n")):
        failures.append(
            "Your diary is missing the YAML frontmatter block. It must start with "
            "---, include date and importance, then end with ---."
        )
        return

    try:
        post = frontmatter.loads(diary)
    except Exception as exc:  # noqa: BLE001 — yaml errors are the most common
        failures.append(
            f"Your diary's YAML frontmatter did not parse ({exc}). Make sure it's "
            "valid YAML between --- markers."
        )
        return

    if not post.metadata:
        failures.append(
            "Your diary is missing the YAML frontmatter block. It must start with "
            "---, include date and importance, then end with ---."
        )
        return

    raw_date = post.metadata.get("date")
    if raw_date is None:
        failures.append(
            "Your diary is missing the 'date' field in its YAML frontmatter. Add it."
        )
    else:
        if isinstance(raw_date, datetime):
            date_str = raw_date.date().isoformat()
        elif isinstance(raw_date, date):
            date_str = raw_date.isoformat()
        else:
            date_str = str(raw_date)
        if date_str != today:
            failures.append(
                f"Your diary's date ({date_str}) doesn't match today's date "
                f"({today}). Set date correctly in the frontmatter."
            )

    raw_imp = post.metadata.get("importance")
    if raw_imp is None:
        failures.append(
            "Your diary is missing the 'importance' field. It must be an integer 1-5."
        )
    elif isinstance(raw_imp, bool) or not isinstance(raw_imp, int):
        failures.append(
            f"Your diary's importance value ({raw_imp!r}) isn't an integer. Must "
            "be an integer 1-5."
        )
    elif raw_imp < 1 or raw_imp > 5:
        failures.append(
            f"Your diary's importance value ({raw_imp}) is out of range. Must be "
            "an integer 1-5."
        )

    body = post.content.strip()
    if len(body) < MIN_DIARY_BODY_LEN:
        failures.append(
            f"Your diary's body is too short ({len(body)} chars). Write at least "
            f"{MIN_DIARY_BODY_LEN} characters of diary content after the frontmatter."
        )


# ---------- <taste> (story_017) ----------
#
# Deliberately different from <site> and <log>: this validation never fails a
# day. Site and diary are hard requirements; the corpus is additive and must
# never be able to take the site down. Everything below returns warnings and the
# entries that survived, and raises nothing.

TASTE_MIN_ENTRIES = 3
TASTE_MAX_ENTRIES = 8
CONFIDENCE_MIN, CONFIDENCE_MAX = 1, 5


def validate_taste(
    raw: str,
    shown_frame_ids: Any,
    today: str,
) -> tuple[list[dict], list[str]]:
    """Parse a <taste> block into appendable entries plus warnings.

    `shown_frame_ids` is the ordered list threaded out of `select_for_date`, not
    the manifest: she does not get to write verdicts about frames she did not
    look at, and "in the manifest" is a much weaker claim than "on screen today".
    """
    warnings: list[str] = []
    shown = set(shown_frame_ids or ())

    if not (raw or "").strip():
        return [], ["No <taste> block in today's output; nothing appended to the preference log."]

    entries: list[dict] = []
    for n, line in enumerate((raw or "").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            warnings.append(f"<taste> line {n} is not valid JSON ({exc}); skipped.")
            continue
        if not isinstance(parsed, dict):
            warnings.append(f"<taste> line {n} is not a JSON object; skipped.")
            continue

        frame_id = parsed.get("frame_id")
        if frame_id not in shown:
            warnings.append(
                f"<taste> line {n} names frame_id {frame_id!r}, which was not shown "
                "today; dropped."
            )
            continue

        verdict = str(parsed.get("verdict") or "").strip()
        if not verdict:
            warnings.append(f"<taste> line {n} ({frame_id}) has an empty verdict; dropped.")
            continue

        confidence = parsed.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, int):
            warnings.append(
                f"<taste> line {n} ({frame_id}) has a non-integer confidence "
                f"({confidence!r}); dropped."
            )
            continue
        if not CONFIDENCE_MIN <= confidence <= CONFIDENCE_MAX:
            warnings.append(
                f"<taste> line {n} ({frame_id}) has confidence {confidence}, outside "
                f"{CONFIDENCE_MIN}-{CONFIDENCE_MAX}; dropped."
            )
            continue

        compared_to = parsed.get("compared_to")
        if compared_to is not None and not isinstance(compared_to, str):
            compared_to = None

        entries.append({
            "date": today,
            "frame_id": frame_id,
            "verdict": verdict,
            "compared_to": compared_to,
            "confidence": confidence,
        })

    if len(entries) > TASTE_MAX_ENTRIES:
        warnings.append(
            f"<taste> had {len(entries)} valid entries; keeping the first "
            f"{TASTE_MAX_ENTRIES} and dropping the rest."
        )
        entries = entries[:TASTE_MAX_ENTRIES]
    elif len(entries) < TASTE_MIN_ENTRIES:
        warnings.append(
            f"<taste> produced only {len(entries)} valid entr"
            f"{'y' if len(entries) == 1 else 'ies'}; at least {TASTE_MIN_ENTRIES} "
            "were asked for."
        )

    return entries, warnings
