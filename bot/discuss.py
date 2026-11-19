"""Discuss a ready book in its thread, from the reader's saved position.

    python3 -m bot.discuss <command> [--thread <id>] [--thread-name <name>] [--parent <channel id>] --data-dir ...

    where                                      the saved position
    set '<position>' [--start | --end]         move the saved position and rebuild the cut
    place '<section>'                          is a section the reader names behind them?
    find '<description>' [--within <section> | --upto <section or N%>]
    read [<key word> ...] [--section <section>]
    check                                      the answer on stdin

The book is the one threads.json records for the thread id or, failing that, the one whose
thread name matches. Exit codes: 0 use the output; 1 (check) the answer failed, one error per
line on stderr; 2 with NOT_A_BOOK_THREAD_LINE on stdout, not a book thread (never under the
books channel); 3 reply with the printed line word for word and stop. A mistyped command,
--help and an index that cannot be read all exit 3.
"""
import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn

from bot import openclaw
from bot.new_thread import NOT_A_BOOK_THREAD_LINE, UNKNOWN_BOOK_LINE, book_thread, read_threads
from core.brief import Position
from core.cut import FALLBACK, check_answer, parse_position, read_cut
from core.reader import (bare_section, find_scenes, fresh_cut, limit_order, move_to, relation, saved_position,
                         section_edge)
from core.split import SplitNotApproved, load_approved

NO_POSITION_LINE = "Where are you? Name the section as the book prints it, and the scene if you know it."
WHERE_IN_LINE = "Where in {label}? Just started, finished, or describe the last scene."
PAST_LINE = "That's past where you are — move there?"
ASK_LIMIT_LINE = 'Up to where? Give a section, or a rough point like "about 40%".'
NO_MATCH_LINE = "No scene I can see matches that. Which section are you in, and which scene if you know it?"
MATCHES_LINE = "Is it one of these?"
BROKEN_LINE = "I can't read this book's index right now."
USAGE_LINE = "Something went wrong on my side. Could you ask that again?"
# The fallback's own words: outside the exact fallback they hint at what the reader hasn't seen.
HEDGES = ("so far", "what you've read", "not yet", "isn't named", "not named")


def _label(approved: dict, pos: Position) -> str:
    return next(u["label"] for u in approved["sections"] if u["order"] == pos.order)


def _where(args: argparse.Namespace, data_dir: Path, book: str) -> int:
    saved = saved_position(data_dir, book)
    if saved is None:
        print(NO_POSITION_LINE)
        return 3
    print(f"Up to {_label(load_approved(data_dir, book), saved)} · scene {saved.scene}.")
    return 0


def _set(args: argparse.Namespace, data_dir: Path, book: str) -> int:
    approved = load_approved(data_dir, book)
    if args.start or args.end:
        upto = section_edge(approved, args.position, "start" if args.start else "end")
    else:
        bare = bare_section(approved, args.position)
        if bare is not None:
            print(WHERE_IN_LINE.format(label=bare))
            return 3
        upto = parse_position(approved, args.position)
    move_to(data_dir, book, upto)
    print(f"Got it. Up to {_label(approved, upto)} · scene {upto.scene}.")
    return 0


def _is_past(data_dir: Path, book: str, section: str) -> int | None:
    """3 after printing the line to reply with when `section` cannot be read now; else None."""
    saved = saved_position(data_dir, book)
    if saved is None:
        print(NO_POSITION_LINE)
        return 3
    if relation(load_approved(data_dir, book), saved, section) == "past":
        print(PAST_LINE)
        return 3
    return None


def _place(args: argparse.Namespace, data_dir: Path, book: str) -> int:
    refused = _is_past(data_dir, book, args.section)
    if refused is not None:
        return refused
    print("ok")
    return 0


def _find(args: argparse.Namespace, data_dir: Path, book: str) -> int:
    approved = load_approved(data_dir, book)
    if args.within is not None:
        lo = hi = limit_order(approved, args.within)
    elif args.upto is not None:
        lo, hi = 1, limit_order(approved, args.upto)
    else:
        saved = saved_position(data_dir, book)
        if saved is None:
            print(ASK_LIMIT_LINE)
            return 3
        lo, hi = 1, saved.order
    matches = find_scenes(data_dir, book, args.description, lo, hi)
    if not matches:
        print(NO_MATCH_LINE)
        return 3
    print(MATCHES_LINE)
    for m in matches:
        print(f'{m.label} · scene {m.scene} — "{m.sentence}"' if m.sentence else f"{m.label} · scene {m.scene}")
    return 0


def _read(args: argparse.Namespace, data_dir: Path, book: str) -> int:
    if saved_position(data_dir, book) is None:
        print(NO_POSITION_LINE)
        return 3
    if args.section is not None:
        refused = _is_past(data_dir, book, args.section)
        if refused is not None:
            return refused
    print(read_cut(data_dir, book, args.words, args.section).rstrip("\n"))
    return 0


def _hedges(answer: str) -> list[str]:
    """An error for each of HEDGES in the answer after its first line, unless that is exactly FALLBACK."""
    body = answer.strip().partition("\n")[2].strip()
    if body == FALLBACK:
        return []
    flat = body.replace("’", "'").lower()
    return [f"{hedge!r}: stop after what you can cite and say nothing about what is missing; or make the "
            f"whole answer after the first line exactly {FALLBACK!r}" for hedge in HEDGES if hedge in flat]


def _check(args: argparse.Namespace, data_dir: Path, book: str) -> int:
    answer = sys.stdin.read()
    pos = fresh_cut(data_dir, book)
    if pos is None:
        print(NO_POSITION_LINE)
        return 3
    errors = check_answer(answer, load_approved(data_dir, book), pos) + _hedges(answer)
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1
    print("ok")
    return 0


class UsageError(Exception):
    pass


class FailClosedParser(argparse.ArgumentParser):
    """Raises UsageError where argparse would exit: its exit 2 must never read as "not a book
    thread", and --help's usage text on stdout with exit 0 must never reach the reader."""

    def error(self, message: str) -> NoReturn:
        raise UsageError(f"{self.prog}: {message}")

    def print_help(self, file=None) -> None:
        super().print_help(sys.stderr)

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        raise UsageError(message or f"{self.prog}: exit {status}")


def data_dir(path: str) -> Path:
    """--data-dir, expanded; ValueError (an argparse usage error) for a home it cannot find."""
    try:
        return Path(path).expanduser()
    except RuntimeError as e:
        raise ValueError(str(e)) from e


def _broken(command: str, e: Exception) -> int:
    # Paths, parse errors and tracebacks are for the log, never for the reader.
    print(f"bot.discuss {command}: {type(e).__name__}: {e}", file=sys.stderr)
    print(BROKEN_LINE)
    return 3


def main(argv: list[str] | None = None) -> int:
    common = FailClosedParser(add_help=False)
    common.add_argument("--thread", help="the thread's id")
    common.add_argument("--thread-name", help="the thread's name, tried when the id is not recorded")
    common.add_argument("--parent", help="the id of the channel the thread sits under")
    common.add_argument("--data-dir", required=True, type=data_dir, help="parent of the per-book folders")
    parser = FailClosedParser(prog="python3 -m bot.discuss", description="Discuss a ready book in its thread.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("where", parents=[common], help="the saved position").set_defaults(run=_where)
    p_set = sub.add_parser("set", parents=[common], help="move the saved position")
    p_set.add_argument("position", help='"<section> · scene <k>", or a bare section with --start / --end')
    edge = p_set.add_mutually_exclusive_group()
    edge.add_argument("--start", action="store_true", help="scene 1 of the section: just started it")
    edge.add_argument("--end", action="store_true", help="the section's last scene: finished it")
    p_set.set_defaults(run=_set)
    p_place = sub.add_parser("place", parents=[common], help="ok when a named section is not past the reader")
    p_place.add_argument("section")
    p_place.set_defaults(run=_place)
    p_find = sub.add_parser("find", parents=[common], help="scenes matching a description, up to a limit")
    p_find.add_argument("description")
    limit = p_find.add_mutually_exclusive_group()
    limit.add_argument("--within", help="search this section only")
    limit.add_argument("--upto", help='search up to this section, or a rough point like "about 40%%"')
    p_find.set_defaults(run=_find)
    p_read = sub.add_parser("read", parents=[common], help="the parts of the cut a question needs")
    p_read.add_argument("words", nargs="*", help="the question's key words")
    p_read.add_argument("--section", help="a section the question names, read whole")
    p_read.set_defaults(run=_read)
    sub.add_parser("check", parents=[common], help="check an answer on stdin").set_defaults(run=_check)
    try:
        args = parser.parse_args(argv)
        books = args.parent == openclaw.BOOKS_CHANNEL_ID
        if args.thread is None and args.thread_name is None and not books:
            parser.error("give --thread, --thread-name or both")
    except UsageError as e:
        print(e, file=sys.stderr)
        print(USAGE_LINE)
        return 3

    try:
        thread = book_thread(args.data_dir, args.thread, args.thread_name)
        book = None if thread is None else read_threads(args.data_dir)[thread]
    except Exception as e:
        return _broken(args.command, e)
    if book is None:
        if books:
            print(UNKNOWN_BOOK_LINE)
            return 3
        print(NOT_A_BOOK_THREAD_LINE)
        return 2
    try:
        return args.run(args, args.data_dir, book)
    except (SplitNotApproved, json.JSONDecodeError, UnicodeDecodeError) as e:
        return _broken(args.command, e)
    except ValueError as e:
        # Only core's own refusals (an unknown section, a bad scene) are worded for the reader.
        print(e)
        return 3
    except Exception as e:
        return _broken(args.command, e)


if __name__ == "__main__":
    sys.exit(main())
