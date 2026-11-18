import json
import os
import subprocess
import sys
from collections.abc import Callable, Collection
from pathlib import Path

import pytest

from bot import index_bot, openclaw
from bot.index_bot import DONE_LINE
from core.extract import INSTRUCTIONS, Step, next_step, slots
from core.split import load_approved
from tests.epub_fixture import divider_book, toc_book
from tests.index_fixture import StubWorker, fixture_split, valid_reply, write_split

BOOK = "fx"
THREAD = "500"
INSTR = INSTRUCTIONS.read_text(encoding="utf-8")
REPO = Path(__file__).resolve().parent.parent


def dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", ""])
    proc.wait()
    return proc.pid


@pytest.fixture
def loop_pid():
    """Starts a live process whose command line reads like `bot.index_bot <command> <book>`."""
    procs = []

    def spawn(book: str = BOOK, command: str = "run") -> int:
        procs.append(subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                                       "bot.index_bot", command, book, "--data-dir", "x"]))
        return procs[-1].pid

    yield spawn
    for proc in procs:
        proc.kill()
        proc.wait()


class FakeOpenClaw:
    """Stands in for every openclaw command; the agent call answers through a StubWorker."""

    def __init__(self, data: Path, book: str = BOOK, worker: Callable[[Step], str] | None = None,
                 down: Collection[tuple[str, int]] = (), envelope: str | None = None) -> None:
        self.data, self.book = data, book
        self.worker: Callable[[Step], str] = worker or StubWorker()
        self.down, self.envelope = down, envelope
        self.calls: list[list[str]] = []
        self.posts: list[str] = []
        self.attempts: list[tuple[str, int]] = []
        self.cwds: list[Path] = []
        self.events: list[str] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(list(args))
        if args[:2] == ["message", "send"]:
            assert args[2:6] == ["--channel", "discord", "--target", f"channel:{THREAD}"]
            self.posts.append(args[args.index("--message") + 1])
            return "{}"
        if args[:3] == ["message", "thread", "create"]:
            self.events.append("create")
            assert (self.data / self.book / f"{self.book}.epub").exists()
            return json.dumps({"threadId": THREAD})
        # OpenClaw's exec-marker guard: under the bot's exec tool a gateway `agent` turn is refused.
        if os.environ.get("OPENCLAW_SHELL") == "exec" and args[0] == "agent" and args[1:2] != ["exec"]:
            raise openclaw.OpenClawError("exit 1: Gateway agent from agent exec would lose inter-session attribution.")
        assert args[:2] == ["agent", "exec"] and args[-1] == "--json"
        assert args[args.index("--model") + 1] == "deepseek-index/deepseek-v4-flash"
        assert args[args.index("--timeout") + 1] == str(index_bot.AGENT_TIMEOUT_S)
        assert not any(a.startswith(("--thinking", "--isolated", "--state-dir", "--session-id")) for a in args)
        cwd = Path(args[args.index("--cwd") + 1])
        assert cwd.is_dir() and not any(cwd.iterdir())
        assert not cwd.resolve().is_relative_to(self.data.resolve())
        self.cwds.append(cwd)
        step = next_step(self.data, self.book, INSTR)
        assert step is not None
        assert Path(args[args.index("--message-file") + 1]).read_text(encoding="utf-8") == step.prompt
        self.attempts.append((step.slot.id, step.attempt))
        if (step.slot.id, step.attempt) in self.down:
            raise openclaw.OpenClawError("exit 1: gateway down")
        if self.envelope is not None:
            return self.envelope
        return json.dumps({"ok": True, "status": "ok", "final": "done",
                           "payloads": [{"text": self.worker(step)}],
                           "usage": {"input": 1, "output": 1}, "model": "deepseek-v4-flash",
                           "provider": "deepseek-index", "sessionId": f"exec-{len(self.attempts)}"})


@pytest.fixture
def data(tmp_path):
    write_split(tmp_path, BOOK, fixture_split())
    (tmp_path / "threads.json").write_text(json.dumps({THREAD: BOOK}), encoding="utf-8")
    return tmp_path


def fake(monkeypatch, data, **kwargs) -> FakeOpenClaw:
    # The loop always runs under the bot's exec tool, which sets this marker.
    monkeypatch.setenv("OPENCLAW_SHELL", "exec")
    stub = FakeOpenClaw(data, **kwargs)
    monkeypatch.setattr(openclaw, "call", stub)
    return stub


def status(data, book=BOOK) -> dict:
    return json.loads((data / book / "status.json").read_text(encoding="utf-8"))


def sections(data):
    return data / BOOK / "sections"


def story_labels(data):
    return [s.section for s in slots(load_approved(data, BOOK)) if not s.chapter0]


# ---- run: the loop ----

def test_run_posts_progress_per_section_and_ends_ready(data, monkeypatch):
    stub = fake(monkeypatch, data)
    assert index_bot.run(data, BOOK) == 0
    labels = story_labels(data)
    assert stub.posts == ["chapter 0 done",
                          *(f"{label} done ({i}/{len(labels)})" for i, label in enumerate(labels, 1)),
                          DONE_LINE]
    assert DONE_LINE == "Ready. Where are you? Give a section and scene."
    assert status(data)["state"] == "ready"
    assert sorted(p.name for p in sections(data).glob("*.json")) == ["s00.json", "s01.json", "s02.json", "s03.json"]


def test_every_attempt_gets_its_own_empty_cwd_and_leaves_none_behind(data, monkeypatch):
    stub = fake(monkeypatch, data, worker=StubWorker({("s02", 1): "not json"}))
    assert index_bot.run(data, BOOK) == 0
    assert [a for a in stub.attempts if a[0] == "s02"] == [("s02", 1), ("s02", 2)]
    assert len(set(stub.cwds)) == len(stub.cwds) == len(stub.attempts) == 5
    assert not any(c.exists() for c in stub.cwds)
    assert "ch 2 done (2/3)" in stub.posts


def test_a_second_rejection_stops_with_a_visible_failure(data, monkeypatch):
    stub = fake(monkeypatch, data, worker=StubWorker({("s02", 1): "not json", ("s02", 2): "still not json"}))
    assert index_bot.run(data, BOOK) == 1
    failure = stub.posts[-1]
    assert "ch 2" in failure and "rejected twice" in failure
    assert "fx/sections/s02.rejected-1.txt" in failure and "fx/sections/s02.rejected-2.txt" in failure
    assert "/index-book fx" in failure
    assert DONE_LINE not in stub.posts
    assert not any(slot == "s03" for slot, _ in stub.attempts)
    assert (status(data)["state"], status(data)["detail"]) == ("failed", failure)


def test_an_openclaw_failure_never_submits_and_a_rerun_resumes_there(data, monkeypatch):
    stub = fake(monkeypatch, data, down={("s02", 1)})
    assert index_bot.run(data, BOOK) == 2
    assert "gateway down" in stub.posts[-1] and "ch 2" in stub.posts[-1]
    assert not list(sections(data).glob("s02.*"))
    assert status(data)["state"] == "failed"
    assert not stub.cwds[-1].exists()

    again = fake(monkeypatch, data)
    assert index_bot.run(data, BOOK) == 0
    assert again.attempts == [("s02", 1), ("s03", 1)]
    assert again.posts == ["ch 2 done (2/3)", "ch 3 done (3/3)", DONE_LINE]


def test_an_unreadable_envelope_is_a_failure_not_a_rejection(data, monkeypatch):
    stub = fake(monkeypatch, data, envelope="Error: not logged in")
    assert index_bot.run(data, BOOK) == 2
    assert "chapter 0" in stub.posts[-1]
    assert not list(sections(data).glob("s00.*"))


def test_status_tracks_the_current_section_while_running(data, monkeypatch):
    seen = []
    stub = fake(monkeypatch, data)
    inner = stub.worker

    def watch(step):
        seen.append({k: status(data)[k] for k in ("state", "at", "done", "total", "pid", "thread")})
        return inner(step)

    stub.worker = watch
    index_bot.run(data, BOOK)
    pid = os.getpid()
    assert seen == [
        {"state": "indexing", "at": "chapter 0", "done": 0, "total": 3, "pid": pid, "thread": THREAD},
        {"state": "indexing", "at": "ch 1", "done": 0, "total": 3, "pid": pid, "thread": THREAD},
        {"state": "indexing", "at": "ch 2", "done": 1, "total": 3, "pid": pid, "thread": THREAD},
        {"state": "indexing", "at": "ch 3", "done": 2, "total": 3, "pid": pid, "thread": THREAD},
    ]


# ---- run: one bracket slip in a reply ----

def asked(step: Step, questions: list[str] | None = None) -> str:
    """A valid compact reply whose last scene has open questions, so it ends `"]}]}` like a real one."""
    reply = valid_reply(step)
    reply["scenes"][-1]["open_questions"] = questions or ["who left the lantern lit?"]
    return json.dumps(reply, ensure_ascii=False, separators=(",", ":"))


def stray(good: str) -> str:
    return good[:-4] + "}" + good[-4:]


def unclosed(good: str) -> str:
    return good[:-4] + good[-3:]


class Slipping:
    """Answers with `asked`; s00's first attempt gets `slip` applied, and both texts are kept."""

    def __init__(self, slip: Callable[[str], str]) -> None:
        self.slip, self.good, self.sent = slip, "", ""

    def __call__(self, step: Step) -> str:
        reply = asked(step)
        if (step.slot.id, step.attempt) == ("s00", 1):
            self.good, self.sent = reply, self.slip(reply)
            return self.sent
        return reply


def first_step(data) -> Step:
    step = next_step(data, BOOK, INSTR)
    assert step is not None
    return step


@pytest.mark.parametrize("slip, note", [
    (stray, "dropped a stray '}' at char {at}"),
    (unclosed, "inserted ']' at char {at}"),
])
def test_one_bracket_slip_is_repaired_logged_and_accepted_first_time(data, monkeypatch, capsys, slip, note):
    worker = Slipping(slip)
    stub = fake(monkeypatch, data, worker=worker)
    assert index_bot.run(data, BOOK) == 0
    assert ("s00", 1) in stub.attempts and ("s00", 2) not in stub.attempts
    assert stub.posts[0] == "chapter 0 done"
    assert not list(sections(data).glob("s00.rejected-*"))
    saved = json.loads((sections(data) / "s00.json").read_text(encoding="utf-8"))
    assert saved == json.loads(worker.good)
    out = capsys.readouterr().out
    expected = f"s00 attempt 1: repaired the reply's JSON ({note.replace('{at}', str(len(worker.good) - 4))})"
    assert out.splitlines() == [expected]


def test_an_unrepairable_reply_reaches_the_validator_unchanged_and_logs_nothing(data, monkeypatch, capsys):
    worker = Slipping(lambda good: good[:-1] + ",}")
    fake(monkeypatch, data, worker=worker)
    assert index_bot.run(data, BOOK) == 0
    assert (sections(data) / "s00.rejected-1.txt").read_text(encoding="utf-8") == worker.sent
    errors = (sections(data) / "s00.rejected-1.errors.txt").read_text(encoding="utf-8")
    assert errors.startswith("the reply is not valid JSON (")
    assert "repaired" not in capsys.readouterr().out


def test_repair_leaves_a_reply_that_already_parses_alone(data):
    assert index_bot._repair_closer(asked(first_step(data))) is None


def test_repair_reads_a_fenced_reply_like_the_validator(data):
    good, fence = asked(first_step(data)), "```json\n"
    repaired = index_bot._repair_closer(fence + stray(good) + "\n```")
    assert repaired == (fence + good + "\n```", f"dropped a stray '}}' at char {len(fence) + len(good) - 4}")


def test_repair_ignores_brackets_inside_strings(data):
    good = asked(first_step(data), ['she wrote "]}" on the door', "a back\\slash ]} here", "ends in \\"])
    assert '\\"]}\\"' in good and "\\\\slash" in good and '\\\\"]' in good
    assert index_bot._repair_closer(stray(good)) == (good, f"dropped a stray '}}' at char {len(good) - 4}")


@pytest.mark.parametrize("damage", [
    pytest.param(lambda good: good[:-2], id="cut off before its last closers"),
    pytest.param(lambda good: "{]" + stray(good)[1:], id="two slips"),
    pytest.param(lambda good: good[:-1] + ",}", id="trailing comma"),
])
def test_repair_gives_up_on_anything_but_one_slip(data, damage):
    assert index_bot._repair_closer(damage(asked(first_step(data)))) is None


# ---- openclaw seam helpers ----

def test_reply_text_reads_the_agent_exec_payload_text():
    envelope = {"ok": True, "status": "ok", "final": "ignored", "payloads": [{"text": "a"}, {"text": "b"}],
                "provider": "deepseek-index", "sessionId": "x"}
    assert openclaw.reply_text(json.dumps(envelope)) == "a\n\nb"
    for bad in ("not json", "{}", json.dumps({"payloads": [{"mediaUrl": "x"}]}), "[]",
                json.dumps({"ok": True, "final": "only final"}),
                json.dumps({"status": "ok", "result": {"payloads": [{"text": "gateway shape"}]}})):
        with pytest.raises(openclaw.OpenClawError):
            openclaw.reply_text(bad)


def test_post_splits_long_text_on_line_boundaries(monkeypatch):
    sent = []
    monkeypatch.setattr(openclaw, "call", lambda args: sent.append(args[args.index("--message") + 1]) or "")
    lines = [f"line {i} " + "x" * 90 for i in range(60)]
    openclaw.post("7", "\n".join(lines))
    assert len(sent) > 1 and all(len(c) <= 1900 for c in sent)
    assert "\n".join(sent) == "\n".join(lines)
    sent.clear()
    openclaw.post("7", "y" * 4000)
    assert [len(c) for c in sent] == [1900, 1900, 200]


def test_call_raises_with_the_first_stderr_line(monkeypatch):
    def run(argv, **kwargs):
        assert argv[0] == "openclaw" and kwargs["capture_output"] and kwargs["text"]
        return subprocess.CompletedProcess(argv, 3, "", "boom\nmore detail\n")

    monkeypatch.setattr(openclaw.subprocess, "run", run)
    with pytest.raises(openclaw.OpenClawError, match=r"exit 3: boom$"):
        openclaw.call(["agent"])


def test_call_raises_with_the_json_error_message_over_the_generic_stderr_line(monkeypatch):
    reason = "Gateway agent from agent exec would lose inter-session attribution."
    stdout = json.dumps({"ok": False, "error": {"type": "cli_error", "message": reason}})

    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout, "[openclaw] The CLI command failed.\nmore\n")

    monkeypatch.setattr(openclaw.subprocess, "run", run)
    with pytest.raises(openclaw.OpenClawError) as e:
        openclaw.call(["agent", "exec", "--json"])
    assert str(e.value) == f"exit 1: {reason}"


# ---- start ----

@pytest.fixture
def inbound(tmp_path):
    folder = tmp_path / "media" / "inbound"
    folder.mkdir(parents=True)
    return folder


@pytest.fixture
def launches(monkeypatch):
    seen = []

    def launch(book, data_dir):
        seen.append((book, Path(data_dir)))
        return os.getpid()

    monkeypatch.setattr(index_bot, "_launch", launch)
    return seen


def test_start_copies_first_opens_the_thread_splits_and_launches_once(tmp_path, inbound, launches, monkeypatch, capsys):
    data = tmp_path / "data"
    epub = toc_book(inbound)
    stub = fake(monkeypatch, data, book="toc")
    assert index_bot.main(["start", "toc", str(epub), "--data-dir", str(data)]) == 0
    assert (data / "toc" / "toc.epub").read_bytes() == epub.read_bytes()
    assert stub.events == ["create"] and stub.calls[0][:3] == ["message", "thread", "create"]
    assert json.loads((data / "threads.json").read_text(encoding="utf-8")) == {THREAD: "toc"}
    assert len(stub.posts) == 1 and stub.posts[0].startswith("Split approved")
    assert launches == [("toc", data)]
    assert (status(data, "toc")["state"], status(data, "toc")["pid"]) == ("indexing", os.getpid())
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and THREAD in out


def test_start_on_an_unapproved_split_posts_the_reasons_and_does_not_launch(tmp_path, inbound, launches, monkeypatch):
    data = tmp_path / "data"
    stub = fake(monkeypatch, data, book="d")
    assert index_bot.main(["start", "d", str(divider_book(inbound, adjacent=True)), "--data-dir", str(data)]) == 1
    assert "no-empty-scene" in stub.posts[-1]
    assert launches == []
    assert status(data, "d")["state"] == "failed"


def test_start_refuses_while_a_live_loop_runs(data, inbound, launches, loop_pid, monkeypatch):
    stub = fake(monkeypatch, data)
    (data / BOOK / "status.json").write_text(json.dumps({"state": "indexing", "at": "ch 2", "pid": loop_pid()}),
                                             encoding="utf-8")
    assert index_bot.main(["start", BOOK, str(toc_book(inbound)), "--data-dir", str(data)]) == 1
    assert stub.calls == [] and launches == []


def test_start_goes_ahead_when_the_recorded_pid_now_belongs_to_another_process(data, launches, monkeypatch):
    (data / BOOK / f"{BOOK}.epub").write_bytes(b"kept copy")
    (data / BOOK / "status.json").write_text(json.dumps({"state": "indexing", "pid": os.getpid()}), encoding="utf-8")
    fake(monkeypatch, data)
    assert index_bot.main(["start", BOOK, "--data-dir", str(data)]) == 0
    assert launches == [(BOOK, data)]


def test_start_resumes_without_rebuilding_the_split(data, launches, monkeypatch):
    (data / BOOK / f"{BOOK}.epub").write_bytes(b"kept copy")
    (data / BOOK / "status.json").write_text(json.dumps({"state": "indexing", "pid": dead_pid()}), encoding="utf-8")
    before = (data / BOOK / "split.json").read_bytes()
    stub = fake(monkeypatch, data)
    assert index_bot.main(["start", BOOK, "--data-dir", str(data)]) == 0
    assert (data / BOOK / "split.json").read_bytes() == before
    assert stub.calls == [] and launches == [(BOOK, data)]


def test_start_without_an_attachment_or_a_saved_copy_asks_for_one(tmp_path, launches, monkeypatch, capsys):
    stub = fake(monkeypatch, tmp_path)
    assert index_bot.main(["start", BOOK, "--data-dir", str(tmp_path)]) == 2
    assert "attach" in capsys.readouterr().out
    assert stub.calls == [] and launches == []


def test_launch_detaches_the_loop_from_the_clone_root_with_the_data_dir(tmp_path, monkeypatch):
    seen = {}

    class Popen:
        pid = 4242

        def __init__(self, argv, **kwargs):
            seen.update(argv=argv, **kwargs)

    monkeypatch.setattr(index_bot.subprocess, "Popen", Popen)
    (tmp_path / BOOK).mkdir()
    assert index_bot._launch(BOOK, tmp_path) == 4242
    assert seen["argv"] == [sys.executable, "-m", "bot.index_bot", "run", BOOK, "--data-dir", str(tmp_path)]
    assert seen["start_new_session"] is True and Path(seen["cwd"]) == REPO
    assert Path(seen["stdout"].name) == tmp_path / BOOK / "run.log"
    # The loop keeps the exec tool's environment, OPENCLAW_SHELL marker included; OpenClaw says never remove it.
    assert seen.get("env") is None


# ---- status ----

def write_status(data, **fields):
    (data / BOOK / "status.json").write_text(json.dumps(fields), encoding="utf-8")


def ask(data, capsys, thread=THREAD):
    code = index_bot.main(["status", "--thread", thread, "--data-dir", str(data)])
    return code, capsys.readouterr().out.strip()


def test_status_while_a_live_loop_indexes(data, loop_pid, capsys):
    write_status(data, state="indexing", at="ch 2", pid=loop_pid())
    assert ask(data, capsys) == (0, "still indexing, at ch 2")


def test_status_while_start_is_still_splitting(data, loop_pid, capsys):
    write_status(data, state="indexing", at="the split", pid=loop_pid(command="start"))
    assert ask(data, capsys) == (0, "still indexing, at the split")


@pytest.mark.parametrize("other", ["pytest", "another book's loop"])
def test_status_of_a_reused_pid_says_it_stopped(data, loop_pid, capsys, other):
    pid = os.getpid() if other == "pytest" else loop_pid(book="other")
    write_status(data, state="indexing", at="ch 2", pid=pid)
    code, line = ask(data, capsys)
    assert code == 0 and "stopped at ch 2" in line


def test_status_of_a_dead_loop_says_it_stopped(data, capsys):
    write_status(data, state="indexing", at="ch 2", pid=dead_pid())
    code, line = ask(data, capsys)
    assert code == 0 and "stopped at ch 2" in line and "/index-book fx" in line


def test_status_of_a_failed_run_repeats_its_failure(data, capsys):
    write_status(data, state="failed", at="ch 2", pid=dead_pid(), detail="Indexing fx stopped at ch 2: boom.")
    assert ask(data, capsys) == (0, "Indexing fx stopped at ch 2: boom.")


def test_status_ready(data, capsys):
    write_status(data, state="ready", pid=dead_pid())
    assert ask(data, capsys) == (0, "ready")


def test_status_of_an_unknown_thread_is_an_error(data, capsys):
    assert index_bot.main(["status", "--thread", "999", "--data-dir", str(data)]) == 2
