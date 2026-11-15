import os
import subprocess
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
# Inherited GIT_DIR / GIT_INDEX_FILE (pytest run from inside a hook) would aim git at this repo.
ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def git(repo, *args, check=True):
    return subprocess.run(["git", *args], cwd=repo, env=ENV, capture_output=True, text=True, check=check)


def head(repo):
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def add(repo, *paths):
    for p in paths:
        f = repo / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("synthetic\n", encoding="utf-8")
    git(repo, "add", "-f", *paths)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "commit.gpgsign", "false")
    add(tmp_path, "README")
    git(tmp_path, "commit", "-q", "-m", "base")
    git(tmp_path, "config", "core.hooksPath", str(HOOKS))
    return tmp_path


def test_hook_is_executable():
    assert os.access(HOOKS / "pre-commit", os.X_OK)


def test_clean_commit_passes(repo):
    before = head(repo)
    add(repo, "notes.txt")
    git(repo, "commit", "-q", "-m", "clean")
    assert head(repo) != before


@pytest.mark.parametrize("paths", [
    ["books/x.txt"],
    ["index/hive/sections/s01.json"],
    ["src/books/y.txt"],
    ["a/b.epub"],
    ["C.EPUB"],
    ["notes.txt", "books/x.txt"],
])
def test_book_content_is_refused(repo, paths):
    before = head(repo)
    add(repo, *paths)
    result = git(repo, "commit", "-q", "-m", "leak", check=False)
    assert result.returncode != 0
    blocked = [p for p in paths if p != "notes.txt"]
    for p in blocked:
        assert p in result.stderr
    assert "notes.txt" not in result.stderr
    assert head(repo) == before


def test_similar_names_are_not_refused(repo):
    add(repo, "bookshelf/a.txt", "core/indexer.py", "epub.md", "books")
    git(repo, "commit", "-q", "-m", "near misses")


def test_deleting_a_leaked_book_file_passes(repo):
    git(repo, "config", "--unset", "core.hooksPath")
    add(repo, "books/leak.txt")
    git(repo, "commit", "-q", "-m", "leak before the hook")
    git(repo, "config", "core.hooksPath", str(HOOKS))
    before = head(repo)
    git(repo, "rm", "-q", "books/leak.txt")
    git(repo, "commit", "-q", "-m", "remove leak")
    assert head(repo) != before
