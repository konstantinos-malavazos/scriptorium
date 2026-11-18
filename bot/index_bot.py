"""Index a book for the bot: one fresh OpenClaw session per section attempt, progress in its thread.

    python3 -m bot.index_bot start <book> [<epub>] --data-dir ~/.openclaw/scriptorium
    python3 -m bot.index_bot run <book> --data-dir ...      (launched by start, detached)
    python3 -m bot.index_bot status --thread <id> --data-dir ...

start and status each print one line for the agent to relay as it stands.
"""
import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from bot import openclaw
from bot.new_thread import book_name, open_thread, read_threads, thread_for
from core.extract import INSTRUCTIONS, Outcome, _write_text, next_step, slots, submit
from core.split import SplitNotApproved, load_approved

DONE_LINE = "Ready. Where are you? Give a section and scene."
STATUS_FILE = "status.json"
# One section's reply is a long structured index; OpenClaw's 600 s default can cut it off mid-reply.
AGENT_TIMEOUT_S = 1800
# Defined in openclaw.json with a 200k context cap; chat's 80k cap is too small for long sections.
# Never pass --thinking alongside it: the model runs at its maximum thinking.
INDEX_MODEL = "deepseek-index/deepseek-v4-flash"
CLONE_ROOT = Path(__file__).resolve().parent.parent


def _resume(book: str) -> str:
    return f"Send /index-book {book} again to resume."


def read_status(data_dir: Path, book: str) -> dict:
    path = data_dir / book / STATUS_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _set_status(data_dir: Path, book: str, **fields: object) -> None:
    current = read_status(data_dir, book)
    current.update(fields)
    _write_text(data_dir / book / STATUS_FILE, json.dumps(current, ensure_ascii=False, indent=1) + "\n")


def _alive(pid: object, book: str) -> bool:
    """True only if `pid` is still this book's `start` or `run`: pids are reused after a container restart."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace").split("\0")
    except OSError:
        return False
    return any(argv[i:i + 3] in (["bot.index_bot", "run", book], ["bot.index_bot", "start", book])
               for i in range(len(argv)))


def _fail(data_dir: Path, book: str, thread: str, line: str) -> None:
    # Status first: if the post fails too, the failure is still on disk for `status`.
    _set_status(data_dir, book, state="failed", detail=line)
    openclaw.post(thread, line)


# ---- run: the loop ----

def run(data_dir: Path, book: str) -> int:
    """Index every slot still missing. 0 = ready, 1 = a slot was rejected twice, 2 = openclaw failed."""
    thread = thread_for(data_dir, book)
    if thread is None:
        raise ValueError(f"no thread recorded for {book}")
    at, failure = "the start", ""
    try:
        instructions = INSTRUCTIONS.read_text(encoding="utf-8")
        story = [s.id for s in slots(load_approved(data_dir, book)) if not s.chapter0]
        while (step := next_step(data_dir, book, instructions)) is not None:
            slot, at = step.slot, step.slot.section
            done = story.index(slot.id) if slot.id in story else 0
            _set_status(data_dir, book, state="indexing", at=at, done=done, total=len(story),
                        pid=os.getpid(), thread=thread, detail="")
            prompt = data_dir / book / "work" / f"{slot.id}.prompt.md"
            _write_text(prompt, step.prompt)
            session = f"{book}-{slot.id}-a{step.attempt}-{secrets.token_hex(4)}"
            try:
                reply = openclaw.reply_text(openclaw.call(
                    ["agent", "--message-file", str(prompt), "--session-id", session,
                     "--model", INDEX_MODEL, "--timeout", str(AGENT_TIMEOUT_S), "--json"]))
            except openclaw.OpenClawError as e:
                # Never submit here: a non-reply would use up the slot's one retry.
                failure = f"Indexing {book} stopped at {at}: openclaw failed ({e}). " + _resume(book)
                _fail(data_dir, book, thread, failure)
                return 2
            result = submit(data_dir, book, slot.id, reply)
            if result.outcome is Outcome.ACCEPTED:
                openclaw.post(thread, "chapter 0 done" if slot.chapter0 else f"{at} done ({done + 1}/{len(story)})")
            elif result.outcome is Outcome.STOPPED:
                saved = " and ".join(f"{book}/sections/{slot.id}.rejected-{n}.txt"
                                     for n in (step.attempt - 1, step.attempt))
                failure = (f"Indexing {book} stopped at {at}: the reply was rejected twice "
                           f"({result.errors[0] if result.errors else 'no detail'}). "
                           f"Saved: {saved}. " + _resume(book))
                _fail(data_dir, book, thread, failure)
                return 1
        openclaw.post(thread, DONE_LINE)
        _set_status(data_dir, book, state="ready", at="", done=len(story), total=len(story), detail="")
        return 0
    except Exception as e:
        if not failure:  # a failure already being reported is on disk; do not overwrite it
            _fail(data_dir, book, thread, f"Indexing {book} stopped at {at}: {type(e).__name__}: {e}. "
                                          + _resume(book))
        raise


# ---- start ----

def _copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}-")
    os.close(fd)
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _outline(copy: Path, book: str, data_dir: Path) -> tuple[int, str]:
    """Run the split; (0, verdict line) when approved, else (nonzero, the reasons)."""
    done = subprocess.run([sys.executable, "-m", "core.split", "outline", str(copy), book,
                           "--data-dir", str(data_dir)], cwd=CLONE_ROOT, capture_output=True, text=True, check=False)
    if done.returncode == 0:
        approved = load_approved(data_dir, book)
        front = ", ".join(u["label"] for u in approved["chapter0"]) or "none"
        return 0, f"Split approved: {len(approved['sections'])} sections; chapter 0: {front}."
    if done.returncode == 1:
        split = json.loads((data_dir / book / "split.json").read_text(encoding="utf-8"))
        return 1, f"Split of {book} not approved: " + "; ".join(split.get("reasons") or ["no reason given"]) + "."
    last = next((line for line in reversed(done.stderr.splitlines()) if line.strip()), "no error output")
    return done.returncode, f"Split of {book} failed: {last.strip()}"


def _launch(book: str, data_dir: Path) -> int:
    """Start `run` in its own session, so it outlives the exec that started it; returns its pid."""
    with open(data_dir / book / "run.log", "a", encoding="utf-8") as log:
        proc = subprocess.Popen([sys.executable, "-m", "bot.index_bot", "run", book, "--data-dir", str(data_dir)],
                                cwd=CLONE_ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)
    return proc.pid


def start(data_dir: Path, book: str, epub: Path | None) -> tuple[int, str]:
    """Copy the EPUB in, open the thread, split if needed, launch the loop. Returns (exit code, line)."""
    status = read_status(data_dir, book)
    if status.get("state") == "indexing" and _alive(status.get("pid"), book):
        return 1, f"{book} is already indexing, at {status.get('at', 'the start')}."
    copy = data_dir / book / f"{book}.epub"
    if epub is not None:
        if not epub.is_file():
            return 2, f"No EPUB at {epub}."
        _copy(epub, copy)
    elif not copy.exists():
        return 2, f"Please attach {book}'s EPUB to /index-book {book}."
    try:
        thread = open_thread(data_dir, book)
    except openclaw.OpenClawError as e:
        return 2, f"Could not open {book}'s thread: {e}"
    _set_status(data_dir, book, state="indexing", at="the split", pid=os.getpid(), thread=thread, detail="")
    try:
        load_approved(data_dir, book)
    except (FileNotFoundError, SplitNotApproved):
        code, line = _outline(copy, book, data_dir)
        if code != 0:
            _fail(data_dir, book, thread, line)
            return 1, line
        openclaw.post(thread, line)
    _set_status(data_dir, book, pid=_launch(book, data_dir))
    return 0, f"Indexing {book}; progress is posted in <#{thread}>."


# ---- status ----

def status_line(data_dir: Path, thread: str) -> str:
    """The line for a message in a book thread: `ready`, or what to reply instead."""
    book = read_threads(data_dir).get(thread)
    if book is None:
        raise LookupError(f"thread {thread} is not a book thread")
    status = read_status(data_dir, book)
    state, at = status.get("state"), status.get("at") or "the start"
    if state == "ready":
        return "ready"
    if state == "failed":
        return status.get("detail") or f"Indexing {book} failed at {at}. " + _resume(book)
    if _alive(status.get("pid"), book):
        return f"still indexing, at {at}"
    return f"Indexing {book} stopped at {at} without finishing. " + _resume(book)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m bot.index_bot", description="Index a book for the bot.")
    sub = parser.add_subparsers(dest="command", required=True)
    data_arg = {"required": True, "type": lambda p: Path(p).expanduser(), "help": "parent of the per-book folders"}
    p_start = sub.add_parser("start", help="copy the EPUB, open the thread, split, launch the loop")
    p_start.add_argument("book", type=book_name)
    p_start.add_argument("epub", nargs="?", type=Path, help="the attached EPUB; omit to resume from the saved copy")
    p_start.add_argument("--data-dir", **data_arg)
    p_run = sub.add_parser("run", help="the indexing loop")
    p_run.add_argument("book", type=book_name)
    p_run.add_argument("--data-dir", **data_arg)
    p_status = sub.add_parser("status", help="the line to reply in a book thread")
    p_status.add_argument("--thread", required=True)
    p_status.add_argument("--data-dir", **data_arg)
    args = parser.parse_args(argv)
    if args.command == "run":
        return run(args.data_dir, args.book)
    if args.command == "status":
        try:
            print(status_line(args.data_dir, args.thread))
        except LookupError as e:
            print(e, file=sys.stderr)
            return 2
        return 0
    code, line = start(args.data_dir, args.book, args.epub)
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
