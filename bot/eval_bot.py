"""Ask the real OpenClaw agent the evaluator's questions, on a copy of the bot's data.

    python3 -m bot.eval_bot prepare <book> --from <data dir> --to <run dir> --questions <claude data dir>
    python3 -m bot.eval_bot ask <book> --data-dir <run dir>
    python3 -m bot.eval_bot recheck <book> --data-dir <run dir>

prepare (host) builds <run dir>/<book> from a copy of the source index, the book's threads.json
entry, a `ready` status and Claude's questions.json, and refuses when the two splits differ in
anything but `source`.

ask (inside the gateway container) takes the current `ask` step from core.evaluate, sets the
reader's position with bot.discuss, sends the question to `openclaw agent` in a fresh session
that is told to use the run dir, saves the reply as the answer, writes the session's fence
record and deletes the session. It never posts to Discord. Exit codes: 0 answered; 2 nothing
asked (nothing to ask, a setup slip or an openclaw failure); 3 the bot gave a setup reply,
nothing saved; 4 the answer is saved but the session's tool calls could not be read, so no
fence record exists for it. A session that will not delete afterwards is a warning on stderr and
leaves the exit code as it was.

recheck (inside the gateway container, since the records hold the container's paths) applies the
fence to every saved fence record's calls again and rewrites each record's `allowed` and `breach`
in place, keeping its calls; it prints one line per record and a count with the HOME it used.
Exit codes: 0 rechecked; 2 nothing rewritten (no fence records, one will not read, holds no calls
or names a home folder other than HOME, or fencing failed).
"""
import argparse
import contextlib
import io
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from bot import discuss, openclaw
from bot.discuss import BROKEN_LINE, NO_POSITION_LINE, USAGE_LINE, data_dir
from bot.index_bot import STATUS_FILE, _resume
from bot.new_thread import NOT_A_BOOK_THREAD_LINE, THREADS_FILE, UNKNOWN_BOOK_LINE, book_name, read_threads, thread_for, thread_name
from core.compare import fence_path
from core.epub import read_epub
from core.evaluate import eval_dir, next_step, save_answer
from core.extract import _write_text
from core.split import SplitNotApproved, load_approved

LIVE_DATA = Path("~/.openclaw/scriptorium").expanduser()
PINNED_CLONE = "~/.openclaw/tools/scriptorium"
AGENT_DB = Path("~/.openclaw/agents/main/agent/openclaw-agent.sqlite").expanduser()
SESSIONS_DIR = Path("~/.openclaw/agents/main/sessions").expanduser()
AGENT = "main"
SCRIPTS = {"bot.index_bot": {"status"}, "bot.discuss": {"where", "set", "place", "find", "read", "check"}}
# Code Mode's own plumbing: the JS wrapper runs the real tools, each recorded as a nested call.
NESTED_TOOL = "openclaw.nested-tool.v1"
# Python 3.11 has no zstd; the container's Node has it, and the transcript store compresses
# large rows (most `read` results) with it.
UNZSTD = ["node", "-e", "process.stdout.write(require('zlib').zstdDecompressSync(require('fs').readFileSync(0)))"]
WHERE_IN = re.compile(re.escape(discuss.WHERE_IN_LINE).replace(re.escape("{label}"), ".+"))
HEREDOC = re.compile(r"<<(-?)\s*(['\"]?)(\w+)\2\s*$")
# An absolute home folder named in a recorded command, e.g. `/home/node`.
HOME_IN = re.compile(r"(?<![\w.~/-])/home/[^/\s'\"\\]+")
# The exit-code reports the fence allows, word for word.
STATUS_ECHOES = ('echo "EXIT:$?"', "echo EXIT:$?", 'echo "EXIT=$?"', "echo EXIT=$?")
# The agent reads its own instructions before running them.
OWN_SKILL = json.dumps({"name": "scriptorium"})


class FenceUnreadable(Exception):
    """The session's tool calls cannot all be read, so the fence cannot be checked."""


def _split_outside_source(data: Path, book: str) -> dict:
    split = json.loads((data / book / "split.json").read_text(encoding="utf-8"))
    split.pop("source", None)
    return split


def prepare(book: str, source: Path, to: Path, questions: Path) -> Path:
    """Build <to>/<book> for a run, or raise ValueError / OSError / SplitNotApproved and leave
    nothing behind."""
    dest = to / book
    if dest.exists():
        raise ValueError(f"{dest} already exists; a run dir is used once")
    load_approved(source, book)
    if _split_outside_source(source, book) != _split_outside_source(questions, book):
        raise ValueError(f"the split in {source} differs from the one in {questions} outside `source`;"
                         " the questions were written for the other")
    questions_file = eval_dir(questions, book) / "questions.json"
    if not questions_file.exists():
        raise ValueError(f"no questions to reuse: {questions_file}")
    threads_from = source if (source / THREADS_FILE).exists() else LIVE_DATA
    threads = {tid: b for tid, b in read_threads(threads_from).items() if b == book}
    if not threads:
        raise ValueError(f"{threads_from / THREADS_FILE} records no thread for {book}")
    epub = next((p for p in (source / book / f"{book}.epub", LIVE_DATA / book / f"{book}.epub") if p.exists()), None)

    to.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(dir=to, prefix=f".{book}-"))
    try:
        shutil.copyfile(source / book / "split.json", stage / "split.json")
        shutil.copytree(source / book / "sections", stage / "sections")
        if epub is not None:
            shutil.copyfile(epub, stage / f"{book}.epub")
        (stage / STATUS_FILE).write_text(json.dumps({"state": "ready"}) + "\n", encoding="utf-8")
        (stage / "eval").mkdir()
        shutil.copyfile(questions_file, stage / "eval" / "questions.json")
        os.rename(stage, dest)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    _write_text(to / THREADS_FILE, json.dumps(read_threads(to) | threads, ensure_ascii=False, indent=2) + "\n")
    load_approved(to, book)
    return dest


def _same_path(a: str, b: str | os.PathLike[str]) -> bool:
    return os.path.normpath(os.path.expanduser(a)) == os.path.normpath(os.path.expanduser(str(b)))


def _expands(line: str) -> bool:
    """True when the shell would expand something in the line: a `$` or backtick outside single quotes."""
    single = double = False
    i = 0
    while i < len(line):
        c = line[i]
        if single:
            single = c != "'"
        elif c == "\\":
            i += 1
        elif c in "$`":
            return True
        elif c == "'" and not double:
            single = True
        elif c == '"':
            double = not double
        i += 1
    return False


def _bash_refuses(command: str) -> bool:
    """True when bash will not parse the command, so running it would run nothing."""
    done = subprocess.run(["bash", "-n", "-c", command], stdin=subprocess.DEVNULL, capture_output=True, check=False)
    return done.returncode != 0


def _allowed_exec(command: str, run_dir: str | os.PathLike[str]) -> bool:
    first, *rest = command.split("\n")
    while rest and not rest[-1].strip():
        rest.pop()
    # The agent reports each exit code itself: `; <echo>`, or the echo as a last line after a
    # heredoc's terminator (a heredoc cannot take the suffix). No other placement.
    first = next((first.removesuffix("; " + e) for e in STATUS_ECHOES if first.endswith("; " + e)), first)
    heredoc = HEREDOC.search(first)
    if heredoc is not None:
        tabs, quoted, delimiter = heredoc.group(1), heredoc.group(2), heredoc.group(3)
        if tabs:
            # Under `<<-` bash strips leading tabs before it looks for the terminator.
            rest = [line.lstrip("\t") for line in rest]
        if rest and rest[-1] in STATUS_ECHOES:
            rest.pop()
        elif quoted and rest and rest[-1] in {f"{delimiter}; {e}" for e in STATUS_ECHOES}:
            # Bash never sees a terminator there: the line is stdin to `check`, and nothing runs.
            rest[-1] = delimiter
        if not rest or rest[-1] != delimiter or delimiter in rest[:-1]:
            return False
        if not quoted and any(_expands(line) for line in rest[:-1]):
            return False
        first = first[:heredoc.start()]
    elif rest:
        return False
    if _expands(first):
        return False
    lexer = shlex.shlex(first, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        # Bash parses a whole line before running any of it, so one it refuses ran nothing.
        # Over several lines the earlier ones would run, so those stay refused.
        return not rest and _bash_refuses(command)
    if len(tokens) < 7 or tokens[0] != "cd" or not _same_path(tokens[1], PINNED_CLONE) or tokens[2] != "&&":
        return False
    if tokens[3:5] != ["python3", "-m"] or tokens[6] not in SCRIPTS.get(tokens[5], ()):
        return False
    if heredoc is not None and tokens[5:7] != ["bot.discuss", "check"]:
        return False
    args = tokens[7:]
    if any(t and all(ch in "();<>|&" for ch in t) for t in args):
        return False
    dirs = [args[i + 1] for i, t in enumerate(args[:-1]) if t == "--data-dir"]
    dirs += [t.split("=", 1)[1] for t in args if t.startswith("--data-dir=")]
    return len(dirs) == 1 and _same_path(dirs[0], run_dir)


def _allowed(call: dict, run_dir: str | os.PathLike[str]) -> bool:
    if call.get("plumbing"):
        return True
    if call["tool"] == "skills_read":
        return call["command"] == OWN_SKILL
    if call["tool"] != "exec":
        return False
    # A Code Mode wrapper run inside another is recorded as its arguments' JSON on one line,
    # exactly as `_command` writes it, with no `arguments` key; each tool its JS runs is
    # recorded and fenced on its own. A shell call is recorded the same way, so only that exact
    # text counts: other JSON-looking text (several lines, other spacing) is fenced as shell.
    # Blind spot: a shell call whose text is that exact line, with a `$(…)` inside a string
    # value, does run the substitution and passes here; text alone cannot tell it from a real
    # wrapper, whose JS holds `${…}` and backticks too. It is the same blind spot as a JS
    # wrapper starting a process that no tool call records.
    if "arguments" not in call:
        try:
            args = json.loads(call["command"])
        except json.JSONDecodeError:
            args = None
        if _plumbing("exec", args) and call["command"] == json.dumps(args, ensure_ascii=False, sort_keys=True):
            return True
    return _allowed_exec(call["command"], run_dir)


def fence_calls(calls: list[dict], run_dir: str | os.PathLike[str]) -> list[dict]:
    """Each call from `tool_calls` with `allowed` added. Allowed are Code Mode's plumbing, an
    exec of one of the two bot scripts from the pinned clone on exactly the run dir, and
    reading the scriptorium skill itself; every other call is not."""
    return [{**c, "allowed": _allowed(c, run_dir)} for c in calls]


def _unzstd(blob: bytes) -> str:
    try:
        done = subprocess.run(UNZSTD, input=blob, capture_output=True, check=False)
    except FileNotFoundError as e:
        raise FenceUnreadable("node is not on PATH to read a compressed transcript row") from e
    if done.returncode != 0:
        raise FenceUnreadable(f"a compressed transcript row would not decompress: {done.stderr.decode(errors='replace')[:200]}")
    return done.stdout.decode("utf-8")


def _session_key(session: str) -> str:
    return f"agent:{AGENT}:explicit:{session}"


def session_events(session: str) -> list[dict]:
    """Every transcript event of the session, in order, read-only from the agent's store."""
    try:
        with contextlib.closing(sqlite3.connect(f"file:{AGENT_DB}?mode=ro", uri=True)) as db:
            windows = [r[0] for r in db.execute("SELECT session_id FROM session_windows WHERE session_key = ?"
                                                " ORDER BY created_at", (_session_key(session),))]
            rows = [r for w in windows for r in db.execute(
                "SELECT event_json, event_zstd FROM transcript_events WHERE session_id = ? ORDER BY seq", (w,))]
    except sqlite3.Error as e:
        raise FenceUnreadable(f"the transcript store would not read: {e}") from e
    if not rows:
        raise FenceUnreadable(f"no transcript for session {session}")
    events = []
    try:
        for text, blob in rows:
            if text is None and not isinstance(blob, bytes):
                raise FenceUnreadable("a transcript row holds neither JSON nor a compressed blob")
            events.append(json.loads(text if text is not None else _unzstd(blob)))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise FenceUnreadable(f"a transcript row is not JSON: {e}") from e
    if not all(isinstance(event, dict) for event in events):
        raise FenceUnreadable("a transcript row is not a JSON object")
    return events


def _command(tool: str, args: object) -> str:
    """A shell call's command text (the agent sometimes passes it as `cmd`); otherwise the
    arguments as JSON, so nothing about the call is lost."""
    if tool == "exec" and isinstance(args, dict):
        text = next((args[k] for k in ("command", "cmd") if isinstance(args.get(k), str)), None)
        if text is not None:
            return text
    return json.dumps(args, ensure_ascii=False, sort_keys=True)


def _call(tool: str, args: object) -> dict[str, object]:
    """The call's record `{"tool", "command"}`, with `"arguments"` (all of them, as JSON) added
    when a shell call carries more than its command text, so nothing about it is lost."""
    command = _command(tool, args)
    call: dict[str, object] = {"tool": tool, "command": command}
    if (tool == "exec" and isinstance(args, dict) and len(args) > 1
            and command in (args.get("command"), args.get("cmd"))):
        call["arguments"] = json.dumps(args, ensure_ascii=False, sort_keys=True)
    return call


def _plumbing(name: object, args: object) -> bool:
    """Code Mode's own top-level calls: the JS wrapper (an exec with `code` and no shell
    command) and `wait`. An exec that carries a command is a shell call, whatever else it has."""
    if name == "wait":
        return True
    return (name == "exec" and isinstance(args, dict) and "code" in args
            and not any(k in args for k in ("command", "cmd")))


def tool_calls(events: list[dict]) -> list[dict]:
    """The session's tool calls `{"tool", "command"}` (see `_call` for `"arguments"`),
    `"plumbing": True` added on Code Mode's own top-level calls: top-level ones, and those Code Mode ran inside its JS wrapper.
    Raises FenceUnreadable when the turn did not finish."""
    calls, last = [], None
    for event in events:
        message = event.get("message") if event.get("type") == "message" else None
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant":
            last = message
            for part in message.get("content") or []:
                if not (isinstance(part, dict) and part.get("type") == "toolCall"):
                    continue
                name, args = part.get("name"), part.get("arguments")
                call = _call(str(name), args)
                if _plumbing(name, args):
                    call["plumbing"] = True
                calls.append(call)
        elif message.get("role") == "custom" and message.get("customType") == NESTED_TOOL:
            details = message.get("details") or {}
            name = str(details.get("toolName"))
            calls.append(_call(name, details.get("input")))
    if last is None or any(isinstance(p, dict) and p.get("type") == "toolCall" for p in last.get("content") or []):
        raise FenceUnreadable("the transcript does not end in the agent's reply")
    return calls


def agent_reply(stdout: str) -> str:
    """The reply text from `openclaw agent --json` (`result.payloads[].text`), "" when it has none."""
    try:
        out = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise openclaw.OpenClawError(f"the agent output is not JSON: {stdout.strip()[:200]}") from e
    result = out.get("result") if isinstance(out, dict) else None
    payloads = result.get("payloads") if isinstance(result, dict) else None
    if not isinstance(payloads, list) or out.get("status") != "ok":
        raise openclaw.OpenClawError(f"no reply in the agent output: {stdout.strip()[:200]}")
    texts = [p["text"] for p in payloads if isinstance(p, dict) and isinstance(p.get("text"), str) and p["text"].strip()]
    return "\n\n".join(texts)


def setup_reply(reply: str, book: str) -> bool:
    """True when the reply says the setup broke rather than answering: the run must stop there."""
    lines = [line.strip() for line in reply.splitlines() if line.strip()]
    if not lines:
        return True
    last = lines[-1]
    return (last in {BROKEN_LINE, USAGE_LINE, NO_POSITION_LINE, NOT_A_BOOK_THREAD_LINE, UNKNOWN_BOOK_LINE}
            or WHERE_IN.fullmatch(last) is not None
            or last.startswith("still indexing")
            or last.startswith(f"Indexing {book} ") and last.endswith(_resume(book)))


def message(question: str, run_dir: Path, thread: str, name: str | None) -> str:
    lines = ["This message was posted in a Discord thread.", f"Thread id: {thread}"]
    if name:
        lines.append(f"Thread name: {name}")
    lines += [f"Parent channel id: {openclaw.BOOKS_CHANNEL_ID}", "",
              f"For this conversation the scriptorium data folder is {run_dir}: in every scriptorium"
              f" command write --data-dir {run_dir} in place of --data-dir ~/.openclaw/scriptorium.",
              "", "The message:", question]
    return "\n".join(lines) + "\n"


def _thread_name(run_dir: Path, book: str) -> str | None:
    epub = run_dir / book / f"{book}.epub"
    if not epub.exists():
        return None
    meta = read_epub(epub)
    return thread_name(meta.title, meta.author, book)


def forget(session: str) -> None:
    """Delete the session, its archived transcript and its memories."""
    key = _session_key(session)
    out = openclaw.call(["sessions", "delete", "--yes", "--json", key])
    try:
        results = json.loads(out).get("results") or []
    except (json.JSONDecodeError, AttributeError) as e:
        raise openclaw.OpenClawError(f"unreadable reply from sessions delete: {out.strip()[:200]}") from e
    for result in results:
        for archived in result.get("archived") or []:
            path = Path(archived)
            if path.parent == SESSIONS_DIR and path.name.startswith(f"{session}."):
                path.unlink(missing_ok=True)
    openclaw.call(["memory", "forget", "--agent", AGENT, "--session", key, "--json"])


def _write_fence(path: Path, record: dict, calls: list[dict]) -> bool:
    """Write `record` with the fenced `calls` and their verdict `breach` to `path`, keeping
    the record's key order; True on a breach. `ask` and `recheck` both write through here."""
    breach = not all(c["allowed"] for c in calls)
    _write_text(path, json.dumps({**record, "breach": breach, "calls": calls}, ensure_ascii=False, indent=2) + "\n")
    return breach


def _ask_in(run_dir: Path, book: str, step: dict, thread: str, session: str) -> int:
    qid = step["id"]
    with tempfile.TemporaryDirectory(prefix="scriptorium-eval-") as tmp:
        message_file = Path(tmp) / "message.txt"
        message_file.write_text(message(step["question"], run_dir, thread, _thread_name(run_dir, book)), encoding="utf-8")
        out = openclaw.call(["agent", "--agent", AGENT, "--session-id", session,
                             "--message-file", str(message_file), "--json"])
    reply = agent_reply(out)
    if setup_reply(reply, book):
        print(f"{qid}: the bot gave a setup reply, nothing saved:")
        print(reply.strip().splitlines()[-1] if reply.strip() else "(an empty reply)")
        return 3
    save_answer(run_dir, book, qid, reply)
    try:
        calls = fence_calls(tool_calls(session_events(session)), run_dir)
    except FenceUnreadable as e:
        print(f"{qid}: answer saved, but cannot check the fence: {e}")
        return 4
    breach = _write_fence(fence_path(run_dir, book, qid), {"id": qid, "session": session}, calls)
    print(f"{qid} answered · fence {'breach' if breach else 'clean'}")
    return 0


def ask(run_dir: Path, book: str) -> int:
    if os.environ.get("OPENCLAW_SHELL") == "exec":
        print("run ask outside the bot's exec tool: openclaw agent refuses under OPENCLAW_SHELL=exec")
        return 2
    step = next_step(run_dir, book)
    if step["step"] != "ask":
        print(f"nothing to ask: the next step is {step['step']}")
        return 2
    thread = thread_for(run_dir, book)
    if thread is None:
        print(f"{run_dir / THREADS_FILE} records no thread for {book}")
        return 2
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        code = discuss.main(["set", step["position"], "--thread", thread, "--data-dir", str(run_dir)])
    if code != 0:
        lines = said.getvalue().strip().splitlines()
        print(f"{step['id']}: could not set the position {step['position']}:")
        print(lines[-1] if lines else f"bot.discuss set exited {code}")
        return 3
    session = f"scriptorium-eval-{book}-{step['id']}-{int(time.time())}"
    code = 2
    try:
        code = _ask_in(run_dir, book, step, thread, session)
    except openclaw.OpenClawError as e:
        print(f"{step['id']}: openclaw failed, nothing saved: {e}")
    finally:
        try:
            forget(session)
        except openclaw.OpenClawError as e:
            print(f"warning: could not delete session {session}: {e}", file=sys.stderr)
    return code


def _fenceable(record: object) -> bool:
    calls = record.get("calls") if isinstance(record, dict) else None
    return isinstance(calls, list) and all(
        isinstance(c, dict) and isinstance(c.get("tool"), str) and isinstance(c.get("command"), str) for c in calls)


def recheck(run_dir: Path, book: str) -> int:
    folder = fence_path(run_dir, book, "x").parent
    paths = sorted(folder.glob("*.json")) if folder.is_dir() else []
    if not paths:
        print(f"bot.eval_bot recheck: no fence records in {folder}", file=sys.stderr)
        return 2
    home = os.path.normpath(os.path.expanduser("~"))
    records = {}
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            print(f"bot.eval_bot recheck: {path} will not read, nothing rewritten: {e}", file=sys.stderr)
            return 2
        if not _fenceable(record):
            print(f"bot.eval_bot recheck: {path} holds no readable calls, nothing rewritten", file=sys.stderr)
            return 2
        # `~` in a command is fenced against this HOME, so records written under another one
        # would all read as breaches.
        other = next((h for c in record["calls"] for h in HOME_IN.findall(c["command"]) if h != home), None)
        if other is not None:
            print(f"bot.eval_bot recheck: {path} names {other}, but HOME here is {home}; run it where ask ran,"
                  " nothing rewritten", file=sys.stderr)
            return 2
        records[path] = record
    fenced = {}
    for path, record in records.items():
        try:
            fenced[path] = fence_calls([{k: v for k, v in c.items() if k != "allowed"} for c in record["calls"]], run_dir)
        except OSError as e:
            print(f"bot.eval_bot recheck: fencing {path} failed, nothing rewritten: {e}", file=sys.stderr)
            return 2
    breaches = flagged = 0
    for path, record in records.items():
        calls = fenced[path]
        breach = _write_fence(path, record, calls)
        breaches += breach
        flagged += sum(not c["allowed"] for c in calls)
        was = record.get("breach")
        line = f"{path.stem} fence {'breach' if breach else 'clean'}"
        if isinstance(was, bool) and was != breach:
            line += f" (was {'breach' if was else 'clean'})"
        print(line)
    print(f"{breaches} of {len(records)} records breach · {flagged} calls flagged · HOME {home}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m bot.eval_bot", description="Ask the bot the evaluator's questions.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_prepare = sub.add_parser("prepare", help="build a run dir from a copy of the data")
    p_prepare.add_argument("book", type=book_name)
    p_prepare.add_argument("--from", dest="source", required=True, type=data_dir, help="the index to score")
    p_prepare.add_argument("--to", required=True, type=data_dir, help="the run dir")
    p_prepare.add_argument("--questions", required=True, type=data_dir, help="the data dir holding Claude's questions.json")
    p_ask = sub.add_parser("ask", help="ask the bot the current question")
    p_ask.add_argument("book", type=book_name)
    p_ask.add_argument("--data-dir", required=True, type=data_dir, help="the run dir")
    p_recheck = sub.add_parser("recheck", help="re-apply the fence to the run's saved records")
    p_recheck.add_argument("book", type=book_name)
    p_recheck.add_argument("--data-dir", required=True, type=data_dir, help="the run dir")
    args = parser.parse_args(argv)
    if args.command == "prepare":
        try:
            dest = prepare(args.book, args.source, args.to, args.questions)
        except (ValueError, OSError, SplitNotApproved) as e:
            print(f"bot.eval_bot prepare: {type(e).__name__}: {e}", file=sys.stderr)
            return 2
        print(f"prepared {dest}")
        return 0
    if args.command == "recheck":
        return recheck(args.data_dir, args.book)
    return ask(args.data_dir, args.book)


if __name__ == "__main__":
    sys.exit(main())
