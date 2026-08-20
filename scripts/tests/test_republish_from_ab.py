"""Tests for scripts/republish_from_ab.py."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.republish_from_ab import latest_archived_date, republish  # noqa: E402
from scripts.write_outputs import FINALIZED_MARKER  # noqa: E402


DATE = "2026-08-18"
REAL_HTML = (REPO_ROOT / "archive" / f"{DATE}.html").read_text()
REAL_DIARY = (REPO_ROOT / "log" / f"{DATE}.md").read_text()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A miniature repo with one archived day, plus a model-ab output for it."""
    for sub in ("archive", "log", "prompts"):
        (tmp_path / sub).mkdir()
    shutil.copy(REPO_ROOT / "facts.json", tmp_path / "facts.json")
    (tmp_path / "archive" / f"{DATE}.html").write_text("<html>old shipped page</html>")
    (tmp_path / "log" / f"{DATE}.md").write_text("---\ndate: 2026-08-18\nimportance: 2\n---\nold")
    (tmp_path / "prompts" / f"{DATE}.md").write_text("the prompt")
    (tmp_path / "index.html").write_text("<html>old shipped page</html>")

    ab = tmp_path / "model-ab" / DATE
    ab.mkdir(parents=True)
    (ab / "opus.html").write_text(REAL_HTML)
    (ab / "opus.diary.md").write_text(REAL_DIARY)
    return tmp_path


def test_dry_run_writes_nothing(repo):
    rc = republish(DATE, "opus", repo, repo / "model-ab", write=False)
    assert rc == 0
    assert (repo / "archive" / f"{DATE}.html").read_text() == "<html>old shipped page</html>"
    assert (repo / "index.html").read_text() == "<html>old shipped page</html>"


def test_write_replaces_page_and_diary(repo):
    rc = republish(DATE, "opus", repo, repo / "model-ab", write=True)
    assert rc == 0
    page = (repo / "archive" / f"{DATE}.html").read_text()
    assert "old shipped page" not in page
    assert (repo / "log" / f"{DATE}.md").read_text() == REAL_DIARY


def test_published_page_is_finalized_not_raw(repo):
    """The whole point of the tool: raw A/B output must not reach the archive."""
    republish(DATE, "opus", repo, repo / "model-ab", write=True)
    page = (repo / "archive" / f"{DATE}.html").read_text()
    assert FINALIZED_MARKER in page
    assert FINALIZED_MARKER not in REAL_HTML  # the A/B copy really was raw


def test_prompt_is_left_alone(repo):
    republish(DATE, "opus", repo, repo / "model-ab", write=True)
    assert (repo / "prompts" / f"{DATE}.md").read_text() == "the prompt"


def test_index_updated_when_target_is_the_newest_day(repo):
    republish(DATE, "opus", repo, repo / "model-ab", write=True)
    assert (repo / "index.html").read_text() == (repo / "archive" / f"{DATE}.html").read_text()


def test_index_untouched_when_a_newer_day_exists(repo):
    """Replacing an older edition must not drag the front page backwards."""
    (repo / "archive" / "2026-08-19.html").write_text("<html>newer day</html>")
    (repo / "index.html").write_text("<html>newer day</html>")
    republish(DATE, "opus", repo, repo / "model-ab", write=True)
    assert (repo / "index.html").read_text() == "<html>newer day</html>"


def test_refuses_when_diary_date_does_not_match(repo):
    (repo / "model-ab" / DATE / "opus.diary.md").write_text(
        "---\ndate: 2026-01-01\nimportance: 2\n---\nwrong day"
    )
    assert republish(DATE, "opus", repo, repo / "model-ab", write=True) == 1
    assert (repo / "archive" / f"{DATE}.html").read_text() == "<html>old shipped page</html>"


def test_refuses_output_that_would_fail_the_daily_gate(repo):
    (repo / "model-ab" / DATE / "opus.html").write_text("<html>too small</html>")
    assert republish(DATE, "opus", repo, repo / "model-ab", write=True) == 1
    assert (repo / "index.html").read_text() == "<html>old shipped page</html>"


def test_refuses_when_ab_output_is_missing(repo):
    assert republish("2026-07-04", "opus", repo, repo / "model-ab", write=True) == 1


def test_latest_archived_date_ignores_the_index(repo):
    (repo / "archive" / "index.html").write_text("<html>archive index</html>")
    (repo / "archive" / "2026-08-19.html").write_text("<html>newer</html>")
    assert latest_archived_date(repo) == "2026-08-19"
