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
    assert (repo / "index.html").read_text() == injected
    assert (repo / "archive" / "2026-04-22.html").read_text() == injected


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
