"""Open a book's Discord thread in #books and record it in threads.json, in one step.

    python3 -m bot.new_thread <book> --data-dir ~/.openclaw/scriptorium

Reads the title and author from <data-dir>/<book>/<book>.epub and prints the thread id.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from bot import openclaw
from core.epub import read_epub
from core.extract import _write_text
from core.split import _valid_book

THREADS_FILE = "threads.json"  # beside the per-book folders: {thread id: book}


def book_name(name: str) -> str:
    """The book name, or ValueError: core's folder-name rule, and never the threads file."""
    if name == THREADS_FILE:
        raise ValueError(f"book name {name!r} is reserved")
    return _valid_book(name)


def thread_name(title: str, author: str, book: str) -> str:
    name = title or book
    return f"{name} — {author}" if author else name


def read_threads(data_dir: str | os.PathLike[str]) -> dict[str, str]:
    path = Path(data_dir) / THREADS_FILE
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def thread_for(data_dir: str | os.PathLike[str], book: str) -> str | None:
    """The thread recorded for this book, or None."""
    return next((tid for tid, b in read_threads(data_dir).items() if b == book), None)


def _find_by_name(name: str) -> str:
    # Active threads only: this runs right after the create, so the new thread is active, and an
    # archived thread of the same name is an older one that must never be picked up by mistake.
    out = openclaw.call(["message", "thread", "list", "--channel", openclaw.CHANNEL,
                         "--guild-id", openclaw.GUILD, "--channel-id", openclaw.BOOKS_CHANNEL_ID, "--json"])
    # Discord ids grow with time, so the largest id is the newest thread of that name.
    ids = [tid for tid, n in openclaw.listed_threads(out) if n == name and tid.isdigit()]
    return max(ids, key=int, default="")


def open_thread(data_dir: str | os.PathLike[str], book: str) -> str:
    """The book's thread id: the recorded one, or a new thread recorded before returning.

    Raises OpenClawError when the thread cannot be created or its id cannot be found;
    nothing is recorded then.
    """
    existing = thread_for(data_dir, book)
    if existing is not None:
        return existing
    meta = read_epub(Path(data_dir) / book / f"{book}.epub")
    name = thread_name(meta.title, meta.author, book)
    out = openclaw.call(["message", "thread", "create", "--channel", openclaw.CHANNEL,
                         "--target", openclaw.BOOKS_CHANNEL, "--thread-name", name,
                         "--auto-archive-min", "10080", "--json"])
    thread_id = openclaw.created_thread_id(out) or _find_by_name(name)
    if not thread_id:
        raise openclaw.OpenClawError(f"no thread id for {name!r} after create and list")
    threads = read_threads(data_dir)
    threads[thread_id] = book
    _write_text(Path(data_dir) / THREADS_FILE, json.dumps(threads, ensure_ascii=False, indent=1) + "\n")
    return thread_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m bot.new_thread", description="Open a book's thread in #books.")
    parser.add_argument("book", type=book_name)
    parser.add_argument("--data-dir", required=True, type=lambda p: Path(p).expanduser())
    args = parser.parse_args(argv)
    try:
        print(open_thread(args.data_dir, args.book))
    except openclaw.OpenClawError as e:
        print(f"could not open the thread: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
