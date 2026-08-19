"""Check that what a generated page says about the archive is true.

normalize_links guarantees archive *links* resolve. This checks the claims
around them: how many days there have been, which date was day N, how
important a day was, and whether a day being described existed at all.

Two severities, because they fail differently:

  HARD — structural claims the page makes in markup: a link target, or an
         entry tagged data-archive-date. These are unambiguous, so a
         mismatch means the site is inventing its own history. Blocks.
  SOFT — claims read out of prose: counts, "Day N" labels, importance
         markers, dates written in text. Real signal, but a miscount
         shouldn't cost a whole day of site, so these are reported and
         shipped.

Design note: Georgia rebuilds the markup from scratch daily, so nothing here
may depend on a particular layout. The precise checks key on
data-archive-date, an invisible attribute the prompt asks her to put on each
archive entry; the rest are text-level and work on any design. If she emits
no tags at all, that absence is itself reported — a check that silently
passes is worse than no check.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as date_cls
from pathlib import Path

import frontmatter
from bs4 import BeautifulSoup

from scripts.normalize_links import archive_date

HARD = "hard"
SOFT = "soft"

ENTRY_ATTR = "data-archive-date"
IMPORTANCE_ATTR = "data-archive-importance"

_MONTHS = {}
for _i, _name in enumerate(
    [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ],
    start=1,
):
    _MONTHS[_name.lower()] = _i
    _MONTHS[_name[:3].lower()] = _i

_ISO_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_MONTH_DAY_RE = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?\s+(\d{1,2})\b",
    re.IGNORECASE,
)
# The trailing lookahead keeps "April 23 Day 2" — a date butting up against
# the next entry's label — from reading as a claim of "23 days".
_COUNT_RE = re.compile(
    r"\b(\d{1,4})\s*(?:days?|entries|editions?|versions?|snapshots?)\b(?!\s*\d)",
    re.IGNORECASE,
)
_TRAILING_MONTH_RE = re.compile(
    r"(?:" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?\s*$",
    re.IGNORECASE,
)
_DAY_N_RE = re.compile(r"\bday\s+(\d{1,4})\b", re.IGNORECASE)
_IMP_CLASS_RE = re.compile(r"^imp-(\d)$")


@dataclass(frozen=True)
class Discrepancy:
    severity: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class ArchiveTruth:
    """What is actually true about the archive, read off disk."""

    dates: list[str]           # sorted, includes today
    importance: dict[str, int]

    @property
    def count(self) -> int:
        return len(self.dates)

    def day_number(self, date_str: str) -> int | None:
        try:
            return self.dates.index(date_str) + 1
        except ValueError:
            return None


def load_truth(
    repo_root: Path,
    today: str,
    today_importance: int | None = None,
) -> ArchiveTruth:
    """Ground truth. today is included — its snapshot isn't written yet."""
    archive = repo_root / "archive"
    dates = {p.stem for p in archive.glob("*.html") if p.name != "index.html"}
    dates.add(today)

    importance: dict[str, int] = {}
    for path in (repo_root / "log").glob("*.md"):
        try:
            value = frontmatter.loads(path.read_text()).metadata.get("importance")
        except Exception:  # noqa: BLE001 — a malformed old log isn't this check's problem
            continue
        if isinstance(value, int) and not isinstance(value, bool):
            importance[path.stem] = value
    if today_importance is not None:
        importance[today] = today_importance

    return ArchiveTruth(dates=sorted(dates), importance=importance)


# ---------- finding what the page claims ----------


def _entries(soup: BeautifulSoup) -> list:
    return soup.select(f"[{ENTRY_ATTR}]")


def _archive_regions(soup: BeautifulSoup) -> list:
    """Elements that are plausibly *about* the archive.

    Scoping the text-level checks matters more than it looks: Georgia's
    sidebar also carries a stats block, and "60 days of silence from Jeff"
    is not a claim about how many days the archive holds. So this stays
    tight — an element that names itself archive, or failing that the
    closest common ancestor of the tagged entries. Never an arbitrary
    ancestor that merely happens to contain the archive.
    """
    named = [
        el
        for el in soup.find_all(True)
        if el.name not in ("html", "body")
        and "archive"
        in " ".join([el.get("id") or ""] + list(el.get("class") or [])).lower()
    ]
    # Outermost among the self-declared ones: #archive wins over the
    # .archive-list nested inside it, so the section's own heading counts.
    outermost = [el for el in named if not any(el in other.descendants for other in named)]
    if outermost:
        return outermost

    entries = soup.select(f"[{ENTRY_ATTR}]")
    if not entries:
        return []

    common = None
    for entry in entries:
        chain = list(entry.parents)
        common = chain if common is None else [a for a in common if a in chain]
    for ancestor in common or []:
        if ancestor.name not in ("html", "body", "[document]"):
            return [ancestor]
    return []


def _claimed_importance(entry) -> int | None:
    raw = entry.get(IMPORTANCE_ATTR)
    if raw is not None:
        try:
            return int(str(raw).strip())
        except ValueError:
            return None
    for cls in entry.get("class") or []:
        match = _IMP_CLASS_RE.match(cls)
        if match:
            return int(match.group(1))
    return None


def _entry_text(entry) -> str:
    """An entry's own words, including the title tooltip."""
    return " ".join([entry.get_text(" ", strip=True), entry.get("title") or ""])


def _resolve_month_day(month: str, day: int, truth: ArchiveTruth) -> str | None:
    """Attach a year to 'June 18'. None if no candidate year is plausible."""
    years = sorted({d[:4] for d in truth.dates})
    candidates = [f"{y}-{_MONTHS[month.lower()]:02d}-{day:02d}" for y in years]
    for candidate in candidates:
        if candidate in truth.dates:
            return candidate
    # Not a real archive day under any year we've run — report the last
    # candidate so the message names a concrete date.
    return candidates[-1] if candidates else None


# ---------- the checks ----------


def verify_archive_claims(
    html: str,
    repo_root: Path,
    today: str,
    today_importance: int | None = None,
) -> list[Discrepancy]:
    """Return every false claim the page makes about the archive."""
    truth = load_truth(repo_root, today, today_importance)
    soup = BeautifulSoup(html, "html.parser")
    found: list[Discrepancy] = []

    _check_links(soup, truth, found)
    entries = _entries(soup)
    _check_entries(entries, truth, found)
    _check_counts(soup, entries, truth, found)
    _check_text_dates(soup, truth, found)
    _check_coverage(soup, truth, entries, found)
    return found


def _check_links(soup, truth: ArchiveTruth, found: list[Discrepancy]) -> None:
    """HARD: a link to a day that isn't there."""
    for tag in soup.find_all("a", href=True):
        date_str = archive_date(tag["href"])
        if date_str is not None and date_str not in truth.dates:
            found.append(Discrepancy(
                HARD,
                f"You linked to {date_str}, but there's no archived site for that "
                f"day. Only link days that exist.",
            ))


def _check_entries(entries, truth: ArchiveTruth, found: list[Discrepancy]) -> None:
    """HARD: a tagged entry for a day that never happened. SOFT: its details."""
    for entry in entries:
        date_str = (entry.get(ENTRY_ATTR) or "").strip()
        if not _ISO_RE.fullmatch(date_str):
            found.append(Discrepancy(
                HARD,
                f"One of your archive entries has {ENTRY_ATTR}=\"{date_str}\", which "
                "isn't a YYYY-MM-DD date. Use the real date.",
            ))
            continue

        if date_str not in truth.dates:
            found.append(Discrepancy(
                HARD,
                f"You rendered an archive entry for {date_str}, but there's no "
                "archived site for that day — you invented it. Some days are "
                "missing from the archive; don't fill the gaps in.",
            ))
            continue

        text = _entry_text(entry)
        day_match = _DAY_N_RE.search(text)
        if day_match:
            claimed = int(day_match.group(1))
            actual = truth.day_number(date_str)
            if actual is not None and claimed != actual:
                found.append(Discrepancy(
                    SOFT,
                    f"You called {date_str} \"Day {claimed}\", but it's day {actual} "
                    "of the archive. Count the days that exist, not calendar days — "
                    "some days are missing.",
                ))

        claimed_imp = _claimed_importance(entry)
        actual_imp = truth.importance.get(date_str)
        if claimed_imp is not None and actual_imp is not None and claimed_imp != actual_imp:
            found.append(Discrepancy(
                SOFT,
                f"You marked {date_str} as importance {claimed_imp}, but that day's "
                f"log says importance {actual_imp}. Use what you actually wrote.",
            ))


def _check_counts(soup, entries, truth: ArchiveTruth, found: list[Discrepancy]) -> None:
    """SOFT: 'N days' claims, and rendering more entries than exist."""
    if entries and len(entries) != truth.count:
        found.append(Discrepancy(
            SOFT,
            f"You rendered {len(entries)} archive entries, but the archive has "
            f"{truth.count} days.",
        ))

    seen: set[int] = set()
    for region in _archive_regions(soup):
        text = region.get_text(" ", strip=True)
        for match in _COUNT_RE.finditer(text):
            # "April 23 days" is a date, not a count.
            if _TRAILING_MONTH_RE.search(text[max(0, match.start() - 12):match.start()]):
                continue
            claimed = int(match.group(1))
            if claimed != truth.count and claimed not in seen:
                seen.add(claimed)
                found.append(Discrepancy(
                    SOFT,
                    f"Your archive section says \"{match.group(0)}\", but the archive "
                    f"has {truth.count} days.",
                ))


def _scan_text(element) -> str:
    """An element's words, including any tooltips inside it."""
    return " ".join([
        element.get_text(" ", strip=True),
        *(el.get("title") or "" for el in element.find_all(title=True)),
    ])


def _mentioned_days(text: str, truth: ArchiveTruth) -> list[tuple[str, str]]:
    """(date, how it was written) for every day the text names, ISO or prose.

    Bounded to the archive's own date range: a date outside it is talking
    about something other than which days this site has run.
    """
    first, last = truth.dates[0], truth.dates[-1]
    seen: set[str] = set()
    out: list[tuple[str, str]] = []

    def add(date_str: str, written_as: str) -> None:
        if date_str and first <= date_str <= last and date_str not in seen:
            seen.add(date_str)
            out.append((date_str, written_as))

    for match in _ISO_RE.finditer(text):
        add(match.group(1), match.group(1))
    for match in _MONTH_DAY_RE.finditer(text):
        add(
            _resolve_month_day(match.group(1), int(match.group(2)), truth) or "",
            f'"{match.group(0)}"',
        )
    return out


def _check_text_dates(soup, truth: ArchiveTruth, found: list[Discrepancy]) -> None:
    """SOFT: dates written out in the archive's own text that never happened."""
    reported: set[str] = set()
    for region in _archive_regions(soup):
        for date_str, written_as in _mentioned_days(_scan_text(region), truth):
            if date_str in truth.dates or date_str in reported:
                continue
            reported.add(date_str)
            found.append(Discrepancy(
                SOFT,
                f"Your archive section mentions {written_as} as an archive day, but "
                f"there's no archived site for {date_str}.",
            ))


def _check_coverage(soup, truth: ArchiveTruth, entries, found: list[Discrepancy]) -> None:
    """SOFT: the page talks about archive days but tagged none of them.

    Without this a page that renders its archive as plain prose — no links,
    no tags, no element naming itself archive — passes every check in
    silence, which reads as "verified" when nothing was verified.

    Naming a day is the trigger, rather than the word "archive": inject_tech
    puts that word in the footer of every page, so keying on it would make
    this fire on pages with no archive content at all.
    """
    if entries:
        return
    has_archive_links = any(
        archive_date(a["href"]) is not None for a in soup.find_all("a", href=True)
    )
    names_a_day = bool(_mentioned_days(_scan_text(soup), truth))
    if has_archive_links or names_a_day or _archive_regions(soup):
        found.append(Discrepancy(
            SOFT,
            f"None of your archive entries carry {ENTRY_ATTR}=\"YYYY-MM-DD\", so the "
            "day numbers and importance markers on this page couldn't be checked. "
            "Add the attribute to each entry — it's invisible and won't affect your "
            "design.",
        ))


def hard_failures(found: list[Discrepancy]) -> list[Discrepancy]:
    return [d for d in found if d.severity == HARD]


def soft_failures(found: list[Discrepancy]) -> list[Discrepancy]:
    return [d for d in found if d.severity == SOFT]
