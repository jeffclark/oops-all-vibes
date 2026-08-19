"""Tests for scripts/write_outputs.py and scripts/build_archive_index.py."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import scripts.write_outputs as wo  # noqa: E402
from scripts.build_archive_index import build_archive_index  # noqa: E402


def _fake_repo(tmp_path: Path) -> Path:
    for sub in ("archive", "log", "feedback", "prompts"):
        (tmp_path / sub).mkdir()
    return tmp_path


HTML = "<!DOCTYPE html><html><body>hello</body></html>"
DIARY = "---\ndate: 2026-04-22\nimportance: 3\n---\n\nbody body body.\n"
PROMPT = "assembled prompt text"


def test_write_outputs_writes_all_four_files(monkeypatch, tmp_path):
    repo = _fake_repo(tmp_path)
    monkeypatch.setattr(wo.subprocess, "run", MagicMock(return_value=MagicMock(returncode=0)))
    wo.write_outputs("2026-04-22", HTML, DIARY, PROMPT, no_commit=True, repo_root=repo)

    # index.html and archive/<date>.html get inject_tech applied; assert
    # Georgia's original body content survived rather than exact match.
    index_html = (repo / "index.html").read_text()
    archive_html = (repo / "archive" / "2026-04-22.html").read_text()
    assert "hello" in index_html
    assert index_html == archive_html
    # Log and prompt saved verbatim
    assert (repo / "log" / "2026-04-22.md").read_text() == DIARY
    assert (repo / "prompts" / "2026-04-22.md").read_text() == PROMPT
    # Archive index regenerated
    assert (repo / "archive" / "index.html").exists()
    # Injection hook fired (footer appended)
    assert "today's prompt" in index_html


def test_write_outputs_no_commit_skips_subprocess(monkeypatch, tmp_path):
    repo = _fake_repo(tmp_path)
    sp = MagicMock()
    monkeypatch.setattr(wo.subprocess, "run", sp)
    wo.write_outputs("2026-04-22", HTML, DIARY, PROMPT, no_commit=True, repo_root=repo)
    sp.assert_not_called()


def test_write_outputs_commit_without_push_when_env_unset(monkeypatch, tmp_path):
    repo = _fake_repo(tmp_path)
    sp = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr(wo.subprocess, "run", sp)
    monkeypatch.delenv("GEORGIA_PUSH", raising=False)
    wo.write_outputs("2026-04-22", HTML, DIARY, PROMPT, repo_root=repo)

    cmds = [call.args[0] for call in sp.call_args_list]
    assert ["git", "add", "-A"] in cmds
    assert ["git", "commit", "-m", "Georgia, 2026-04-22"] in cmds
    # No push
    assert not any("push" in c for c in cmds)


def test_write_outputs_commit_and_push_when_env_set(monkeypatch, tmp_path):
    repo = _fake_repo(tmp_path)
    sp = MagicMock(return_value=MagicMock(returncode=0, stderr=b""))
    monkeypatch.setattr(wo.subprocess, "run", sp)
    monkeypatch.setenv("GEORGIA_PUSH", "true")
    wo.write_outputs("2026-04-22", HTML, DIARY, PROMPT, repo_root=repo)

    cmds = [call.args[0] for call in sp.call_args_list]
    assert ["git", "add", "-A"] in cmds
    assert ["git", "commit", "-m", "Georgia, 2026-04-22"] in cmds
    assert ["git", "push", "origin", "main"] in cmds


def test_write_outputs_push_failure_raises(monkeypatch, tmp_path):
    repo = _fake_repo(tmp_path)

    # git add + commit succeed, push fails
    def fake_run(cmd, cwd=None, check=False, capture_output=False):
        if "push" in cmd:
            return MagicMock(returncode=1, stderr=b"nope")
        return MagicMock(returncode=0)

    monkeypatch.setattr(wo.subprocess, "run", fake_run)
    monkeypatch.setenv("GEORGIA_PUSH", "1")
    with pytest.raises(RuntimeError, match="git push failed"):
        wo.write_outputs("2026-04-22", HTML, DIARY, PROMPT, repo_root=repo)


def test_write_outputs_inject_tech_called_if_module_present(monkeypatch, tmp_path):
    repo = _fake_repo(tmp_path)
    monkeypatch.setattr(wo.subprocess, "run", MagicMock(return_value=MagicMock(returncode=0)))

    # Fake a scripts.inject_tech module at import time
    injected = "<!-- injected -->" + HTML

    fake_module = type(sys)("scripts.inject_tech")

    def fake_inject(html, date_str, code):
        assert date_str == "2026-04-22"
        return injected

    fake_module.inject_tech = fake_inject
    monkeypatch.setitem(sys.modules, "scripts.inject_tech", fake_module)

    wo.write_outputs("2026-04-22", HTML, DIARY, PROMPT, no_commit=True, repo_root=repo)
    written = (repo / "index.html").read_text()
    assert written == injected + wo.FINALIZED_MARKER
    assert (repo / "archive" / "2026-04-22.html").read_text() == written


# ---------- build_archive_index ----------


def test_build_archive_index_lists_reverse_chron(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    for d in ("2026-04-20", "2026-04-21", "2026-04-22"):
        (archive / f"{d}.html").write_text("<html></html>")
    build_archive_index(tmp_path)
    out = (archive / "index.html").read_text()
    assert "Archive — oops-all-vibes" in out
    # Reverse-chron: 22 before 21 before 20
    i_22 = out.index("2026-04-22")
    i_21 = out.index("2026-04-21")
    i_20 = out.index("2026-04-20")
    assert i_22 < i_21 < i_20
    assert 'href="/"' in out


def test_build_archive_index_excludes_self(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "index.html").write_text("<html>old</html>")
    (archive / "2026-04-22.html").write_text("<html></html>")
    build_archive_index(tmp_path)
    out = (archive / "index.html").read_text()
    # index.html must not link to itself
    assert 'href="./index.html"' not in out
    assert "2026-04-22" in out


def test_build_archive_index_empty_archive_still_writes(tmp_path):
    (tmp_path / "archive").mkdir()
    build_archive_index(tmp_path)
    assert (tmp_path / "archive" / "index.html").exists()


# ---------- link normalization in the pipeline ----------


LINKY_HTML = (
    "<!DOCTYPE html><html><body>hello"
    '<a href="/2026-04-20">root-relative</a>'
    '<a href="/archive/2026-04-21">extensionless</a>'
    '<a href="https://jeff.clarkle.com/archive/2026-04-20">wrong host</a>'
    '<a class="archive-link" href="/2026-01-01">no snapshot</a>'
    '<a href="/2026-04-22">today</a>'
    '<a href="mailto:jeff@clarkle.com">mail</a>'
    "</body></html>"
)


def _write_linky(tmp_path: Path):
    repo = _fake_repo(tmp_path)
    for d in ("2026-04-20", "2026-04-21"):
        (repo / "archive" / f"{d}.html").write_text("<html></html>")
    wo.write_outputs("2026-04-22", LINKY_HTML, DIARY, PROMPT, no_commit=True, repo_root=repo)
    return repo


def test_pipeline_canonicalizes_every_archive_link(tmp_path):
    repo = _write_linky(tmp_path)
    out = (repo / "index.html").read_text()
    assert 'href="/archive/2026-04-20.html"' in out
    assert 'href="/archive/2026-04-21.html"' in out
    assert 'href="/2026-04-20"' not in out
    assert "jeff.clarkle.com" not in out


def test_pipeline_links_today_even_though_file_not_written_yet(tmp_path):
    repo = _write_linky(tmp_path)
    assert 'href="/archive/2026-04-22.html"' in (repo / "index.html").read_text()


def test_pipeline_de_links_dates_with_no_snapshot(tmp_path):
    repo = _write_linky(tmp_path)
    out = (repo / "index.html").read_text()
    assert 'href="/2026-01-01"' not in out
    assert '<span class="archive-link">no snapshot</span>' in out


def test_pipeline_leaves_other_links_alone(tmp_path):
    repo = _write_linky(tmp_path)
    assert 'href="mailto:jeff@clarkle.com"' in (repo / "index.html").read_text()


def test_normalized_index_and_snapshot_stay_byte_identical(tmp_path):
    repo = _write_linky(tmp_path)
    assert (repo / "index.html").read_text() == (repo / "archive" / "2026-04-22.html").read_text()


def test_normalizer_failure_never_costs_the_day(monkeypatch, tmp_path):
    """run_georgia records committed=True before calling write_outputs, so a
    raise in here would mean no site while stats claim one shipped."""
    repo = _fake_repo(tmp_path)

    def boom(html, dates):
        raise ValueError("kaboom")

    monkeypatch.setattr(wo, "normalize_links", boom)
    wo.write_outputs("2026-04-22", LINKY_HTML, DIARY, PROMPT, no_commit=True, repo_root=repo)

    out = (repo / "index.html").read_text()
    assert "hello" in out
    assert 'href="/2026-04-20"' in out  # raw, unrewritten — but shipped


def test_available_dates_includes_today_and_excludes_archive_index(tmp_path):
    repo = _fake_repo(tmp_path)
    (repo / "archive" / "2026-04-20.html").write_text("<html></html>")
    (repo / "archive" / "index.html").write_text("<html></html>")
    assert wo._available_dates(repo, "2026-04-22") == {"2026-04-20", "2026-04-22"}


def test_finalize_html_is_idempotent(tmp_path):
    """write_outputs calls it unconditionally, so run_georgia's already-
    finalized page must pass through untouched rather than gaining a second
    footer. This replaced an already_finalized flag a caller could get wrong."""
    repo = _fake_repo(tmp_path)
    once = wo.finalize_html(HTML, "2026-04-22", repo)
    assert wo.finalize_html(once, "2026-04-22", repo) is once
    assert once.count("today's prompt") == 1


def test_write_outputs_does_not_re_finalize_a_finalized_page(monkeypatch, tmp_path):
    repo = _fake_repo(tmp_path)
    final = wo.finalize_html(LINKY_HTML, "2026-04-22", repo)
    wo.write_outputs("2026-04-22", final, DIARY, PROMPT, no_commit=True, repo_root=repo)
    written = (repo / "index.html").read_text()
    assert written == final
    assert written.count("today's prompt") == 1
