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
UNKNOWN_BOOK_LINE = "I can't tell which book this thread is for."
# The only output on which bot/SKILL.md carries on as usual, and only with exit 2: an exit 2
# from the shell or a parser prints no such line, so it fails closed.
NOT_A_BOOK_THREAD_LINE = "not a book thread"


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


def book_for_name(data_dir: str | os.PathLike[str], name: str) -> str | None:
    """The recorded book whose thread name, recomputed from its saved EPUB, is `name`; or None."""
    for book in dict.fromkeys(read_threads(data_dir).values()):
        epub = Path(data_dir) / book / f"{book}.epub"
        if not epub.exists():
            continue
        meta = read_epub(epub)
        if thread_name(meta.title, meta.author, book) == name.strip():
            return book
    return None


def book_thread(data_dir: str | os.PathLike[str], thread: str | None = None,
                name: str | None = None) -> str | None:
    """The recorded thread id for a message's thread: its id when threads.json holds it, else
    the id recorded for the book its name matches, else None. Never writes threads.json."""
    if thread is not None and thread in read_threads(data_dir):
        return thread
    if name:
        book = book_for_name(data_dir, name)
        if book is not None:
            return thread_for(data_dir, book)
    return None


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
