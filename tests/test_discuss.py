import io
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from bot import discuss, openclaw
from bot.discuss import (ASK_LIMIT_LINE, BROKEN_LINE, MATCHES_LINE, NO_MATCH_LINE, NO_POSITION_LINE, PAST_LINE,
                         USAGE_LINE, WHERE_IN_LINE)
from bot.new_thread import NOT_A_BOOK_THREAD_LINE, UNKNOWN_BOOK_LINE
from core.cut import FALLBACK
from core.extract import run
from core.reader import LIMIT_HINT, READER_FILE
from tests.epub_fixture import write_epub
from tests.index_fixture import StubWorker, fixture_split, write_split

BOOK = "fx"
THREAD = "500"
NAME = "Fx Book — Some Writer"
INSTRUCTIONS = "# Worker instructions\n\nReply with one JSON object."
REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "bot" / "SKILL.md"


@pytest.fixture
def data(tmp_path):
    write_split(tmp_path, BOOK, fixture_split())
    run(tmp_path, BOOK, INSTRUCTIONS, StubWorker())
    write_epub(tmp_path / BOOK / f"{BOOK}.epub", [("a.xhtml", "<p>x</p>")], title="Fx Book", authors=["Some Writer"])
    (tmp_path / "threads.json").write_text(json.dumps({THREAD: BOOK}), encoding="utf-8")
    return tmp_path


def say(data, capsys, *argv, thread=("--thread", THREAD)):
    code = discuss.main([*argv, *thread, "--data-dir", str(data)])
    out = capsys.readouterr()
    return code, out.out.rstrip("\n"), out.err


def check(data, capsys, monkeypatch, answer):
    monkeypatch.setattr("sys.stdin", io.StringIO(answer))
    return say(data, capsys, "check")


def saved(data):
    path = data / BOOK / READER_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def saved_order(data) -> int | None:
    reader = saved(data)
    return None if reader is None else reader["order"]


# --- finding the book ---

def test_a_thread_nobody_recorded_is_not_a_book_thread(data, capsys):
    unrecorded = (2, NOT_A_BOOK_THREAD_LINE, "")
    assert say(data, capsys, "where", thread=("--thread", "999")) == unrecorded
    assert say(data, capsys, "where", thread=("--thread", "999", "--thread-name", "Other")) == unrecorded


def test_the_book_is_found_by_thread_name_when_the_id_is_unknown(data, capsys):
    assert say(data, capsys, "set", "ch 2 · scene 1", thread=("--thread", "999", "--thread-name", NAME))[:2] == (
        0, "Got it. Up to ch 2 · scene 1.")
    assert say(data, capsys, "where", thread=("--thread-name", NAME))[:2] == (0, "Up to ch 2 · scene 1.")


def test_a_thread_id_or_name_is_required(data, capsys):
    code, out, err = say(data, capsys, "where", thread=())
    assert (code, out) == (3, USAGE_LINE)
    assert "--thread" in err


@pytest.mark.parametrize("argv", [
    pytest.param(["read", "Kell", "--upto", "ch 2"], id="find's flag on read"),
    pytest.param(["read", "-Kell"], id="a key word that starts with a dash"),
    pytest.param(["read", "--section"], id="--section with no value"),
    pytest.param(["find", "the fight", "--within", "ch 1", "--upto", "ch 2"], id="both limits"),
    pytest.param(["set", "ch 2", "--start", "--end"], id="start and end"),
    pytest.param(["place"], id="place with no section"),
    pytest.param(["refresh"], id="no such command"),
    pytest.param(["recap", "ch 1"], id="recap takes no position"),
    pytest.param(["where", "--help"], id="help on a command"),
    pytest.param(["-h"], id="help on the script"),
])
def test_a_mistyped_command_answers_nothing_and_never_exits_2(data, capsys, argv):
    say(data, capsys, "set", "ch 2 · scene 1")
    code, out, err = say(data, capsys, *argv)
    assert (code, out) == (3, USAGE_LINE)
    assert err
    assert saved_order(data) == 2


def test_an_unplaced_thread_under_the_books_channel_gets_the_refusal_line(data, capsys):
    books = ("--parent", openclaw.BOOKS_CHANNEL_ID)
    assert say(data, capsys, "where", thread=books)[:2] == (3, UNKNOWN_BOOK_LINE)
    assert say(data, capsys, "where", thread=("--thread", "999", "--thread-name", "Other", *books))[:2] == (
        3, UNKNOWN_BOOK_LINE)
    assert say(data, capsys, "where", thread=("--parent", "123"))[:2] == (3, USAGE_LINE)


def test_a_reader_file_missing_a_key_gives_the_broken_line(data, capsys):
    (data / BOOK / READER_FILE).write_text(json.dumps({"order": 2}), encoding="utf-8")
    code, out, err = say(data, capsys, "where")
    assert (code, out) == (3, BROKEN_LINE)
    assert "Traceback" not in err


def test_an_unreadable_epub_gives_the_broken_line_when_finding_by_name(data, capsys):
    (data / BOOK / f"{BOOK}.epub").write_bytes(b"not a zip")
    code, out, err = say(data, capsys, "where", thread=("--thread", "999", "--thread-name", NAME))
    assert (code, out) == (3, BROKEN_LINE)
    assert err


def test_a_broken_index_never_shows_the_reader_a_path(data, capsys):
    (data / "threads.json").write_text(json.dumps({THREAD: "gone"}), encoding="utf-8")
    code, out, err = say(data, capsys, "set", "ch 1 · scene 1")
    assert (code, out) == (3, BROKEN_LINE)
    assert str(data) in err


# --- set and where ---

def test_set_saves_the_position_and_where_reads_it_back(data, capsys):
    assert say(data, capsys, "where")[:2] == (3, NO_POSITION_LINE)
    assert say(data, capsys, "set", "ch 2 · scene 1")[:2] == (0, "Got it. Up to ch 2 · scene 1.")
    assert saved(data) == {"label": "ch 2", "scene": 1, "order": 2}
    assert say(data, capsys, "where")[:2] == (0, "Up to ch 2 · scene 1.")


def test_a_bare_section_asks_where_in_it_and_moves_nothing(data, capsys):
    say(data, capsys, "set", "ch 1 · scene 1")
    assert say(data, capsys, "set", "ch 3")[:2] == (3, WHERE_IN_LINE.format(label="ch 3"))
    assert WHERE_IN_LINE.format(label="ch 3") == "Where in ch 3? Just started, finished, or describe the last scene."
    assert saved_order(data) == 1


@pytest.mark.parametrize("edge, line", [("--start", "Got it. Up to ch 3 · scene 1."),
                                        ("--end", "Got it. Up to ch 3 · scene 3.")])
def test_just_started_and_finished_take_the_first_and_last_scene(data, capsys, edge, line):
    assert say(data, capsys, "set", "ch 3", edge)[:2] == (0, line)


def test_moving_back_is_allowed(data, capsys):
    say(data, capsys, "set", "ch 3 · scene 2")
    assert say(data, capsys, "set", "ch 1 · scene 2")[:2] == (0, "Got it. Up to ch 1 · scene 2.")
    assert saved_order(data) == 1


@pytest.mark.parametrize("text, words", [("ch 9 · scene 1", "unknown section"), ("ch 3 · scene 7", "which scene?"),
                                         ("Cast", "which scene?")])
def test_a_position_the_book_does_not_have_is_asked_again_and_saves_nothing(data, capsys, text, words):
    code, out, _ = say(data, capsys, "set", text)
    assert code == 3 and words in out and "ch 2" not in out and "ch 3," not in out
    assert saved(data) is None


def test_an_unknown_section_with_start_is_refused(data, capsys):
    code, out, _ = say(data, capsys, "set", "ch 9", "--start")
    assert code == 3 and "unknown section" in out and saved(data) is None


# --- place ---

def test_place_says_ok_at_or_behind_and_asks_before_a_section_past_the_reader(data, capsys):
    assert say(data, capsys, "place", "ch 1")[:2] == (3, NO_POSITION_LINE)
    say(data, capsys, "set", "ch 2 · scene 1")
    for section in ("ch 1", "ch 2", "Cast"):
        assert say(data, capsys, "place", section)[:2] == (0, "ok")
    assert say(data, capsys, "place", "ch 3")[:2] == (3, PAST_LINE)
    assert PAST_LINE == "That's past where you are — move there?"
    code, out, _ = say(data, capsys, "place", "ch 9")
    assert code == 3 and "unknown section" in out
    assert saved_order(data) == 2


# --- find ---

def test_find_with_no_limit_asks_up_to_where(data, capsys):
    assert say(data, capsys, "find", "Tamsin crossed a bridge")[:2] == (3, ASK_LIMIT_LINE)


def test_find_shows_label_and_first_sentence_only(data, capsys):
    code, out, _ = say(data, capsys, "find", "quillfeather bridge", "--within", "ch 1")
    lines = out.splitlines()
    assert code == 0 and lines[0] == MATCHES_LINE
    assert lines[1] == 'ch 1 · scene 1 — "Tamsin Rook crossed the quillfeather bridge at dusk."'
    assert "before nightfall" not in out  # the indexed summary is never shown
    assert saved(data) is None  # a match moves nothing until the reader says yes


def test_find_defaults_to_the_saved_section_and_never_opens_a_later_one(data, capsys):
    say(data, capsys, "set", "ch 2 · scene 1")
    (data / BOOK / "sections" / "s03.json").write_text("{not json", encoding="utf-8")
    assert say(data, capsys, "find", "Ilse Marrow thornwick")[:2] == (3, NO_MATCH_LINE)
    code, out, _ = say(data, capsys, "find", "Corvin brambleglass")
    assert code == 0 and "ch 2 · scene 1" in out


def test_find_up_to_a_percentage_and_refuses_anything_else(data, capsys):
    code, out, _ = say(data, capsys, "find", "Tamsin quillfeather", "--upto", "about 10%")
    assert code == 0 and out.splitlines()[1].startswith("ch 1 · scene")
    assert say(data, capsys, "find", "Tamsin", "--upto", "part two")[:2] == (3, LIMIT_HINT)


# --- read ---

def test_read_needs_a_position(data, capsys):
    assert say(data, capsys, "read", "Tamsin")[:2] == (3, NO_POSITION_LINE)


def test_read_opens_with_the_position_line_and_holds_nothing_past_it(data, capsys):
    say(data, capsys, "set", "ch 2 · scene 1")
    code, out, _ = say(data, capsys, "read", "Tamsin", "Ilse")
    assert code == 0 and out.splitlines()[0] == "Up to ch 2 · scene 1:"
    assert "Tamsin" in out and "Ilse" not in out and "thornwick" not in out


def test_read_a_named_section_whole_or_refuse_one_past_the_reader(data, capsys):
    say(data, capsys, "set", "ch 2 · scene 1")
    code, out, _ = say(data, capsys, "read", "--section", "ch 1")
    assert code == 0 and "## ch 1" in out
    assert say(data, capsys, "read", "anything", "--section", "ch 3")[:2] == (3, PAST_LINE)


def test_recap_needs_a_position(data, capsys):
    assert say(data, capsys, "recap")[:2] == (3, NO_POSITION_LINE)


def test_recap_outside_a_book_thread_exits_2(data, capsys):
    assert say(data, capsys, "recap", thread=("--thread", "999")) == (2, NOT_A_BOOK_THREAD_LINE, "")


def test_recap_opens_with_the_position_line_and_holds_nothing_past_it(data, capsys):
    say(data, capsys, "set", "ch 2 · scene 1")
    code, out, _ = say(data, capsys, "recap")
    assert code == 0 and out.splitlines()[0] == "Up to ch 2 · scene 1:"
    assert "[ch 1 · 1]" in out and "[ch 2 · 1]" in out
    assert "ch 3" not in out and "Ilse" not in out and "thornwick" not in out
    assert "so far" not in out.lower()


def test_recap_stops_at_the_readers_scene(data, capsys):
    say(data, capsys, "set", "ch 3 · scene 2")
    code, out, _ = say(data, capsys, "recap")
    assert code == 0 and out.splitlines()[0] == "Up to ch 3 · scene 2:"
    assert "[ch 3 · 2]" in out and "[ch 3 · 3]" not in out


def test_recap_rebuilds_a_cut_that_drifted_from_the_saved_position(data, capsys):
    say(data, capsys, "set", "ch 2 · scene 1")
    (data / BOOK / "cut" / "position.json").unlink()
    assert say(data, capsys, "recap")[0] == 0
    assert (data / BOOK / "cut" / "position.json").exists()


# --- check ---

def test_check_passes_a_cited_answer_and_the_fallback(data, capsys, monkeypatch):
    say(data, capsys, "set", "ch 2 · scene 1")
    good = "Up to ch 2 · scene 1:\nTamsin Rook crossed the bridge [ch 1 · 1].\n"
    assert check(data, capsys, monkeypatch, good)[:2] == (0, "ok")
    fallback = f"Up to ch 2 · scene 1:\n{FALLBACK}\n"
    assert check(data, capsys, monkeypatch, fallback)[:2] == (0, "ok")


@pytest.mark.parametrize("answer", ["Up to ch 2 · scene 1:\nIlse sells rope [ch 3 · 1].",
                                    "Up to ch 3 · scene 1:\nTamsin crossed [ch 1 · 1].",
                                    "Up to ch 2 · scene 1:\nTamsin crossed a bridge."])
def test_check_fails_an_answer_past_the_reader_or_off_the_rules(data, capsys, monkeypatch, answer):
    say(data, capsys, "set", "ch 2 · scene 1")
    code, out, err = check(data, capsys, monkeypatch, answer)
    assert (code, out) == (1, "") and err.strip()


@pytest.mark.parametrize("hedge", ["No name for him appears in what you've read so far.",
                                   "No name for him appears in what you’ve read so far.",
                                   "Not named yet.",
                                   "He isn't named in what you've read so far.",
                                   "He isn’t NAMED here."])
def test_check_fails_a_cited_answer_that_hedges_about_what_the_reader_has_not_seen(data, capsys, monkeypatch, hedge):
    say(data, capsys, "set", "ch 2 · scene 1")
    answer = f"Up to ch 2 · scene 1:\nAn old ferryman [ch 1 · 1]. {hedge}\n"
    code, out, err = check(data, capsys, monkeypatch, answer)
    assert (code, out) == (1, "") and "say nothing about what is missing" in err


@pytest.mark.parametrize("answer", [f"Up to ch 2 · scene 1:\n{FALLBACK}\n",
                                    "Up to ch 2 · scene 1:\nTamsin later says the bridge is old [ch 1 · 1].\n",
                                    "Up to ch 2 · scene 1:\nTamsin Rook crossed the bridge [ch 1 · 1].\n"])
def test_check_passes_the_exact_fallback_a_narrative_later_and_a_plain_cited_answer(data, capsys, monkeypatch, answer):
    say(data, capsys, "set", "ch 2 · scene 1")
    assert check(data, capsys, monkeypatch, answer)[:2] == (0, "ok")


def test_check_rebuilds_a_cut_that_drifted_from_the_saved_position(data, capsys, monkeypatch):
    say(data, capsys, "set", "ch 2 · scene 1")
    (data / BOOK / "cut" / "position.json").unlink()
    good = "Up to ch 2 · scene 1:\nTamsin Rook crossed the bridge [ch 1 · 1].\n"
    assert check(data, capsys, monkeypatch, good)[:2] == (0, "ok")
    assert (data / BOOK / "cut" / "position.json").exists()


def test_check_needs_a_position(data, capsys, monkeypatch):
    assert check(data, capsys, monkeypatch, "x")[:2] == (3, NO_POSITION_LINE)


# --- the skill ---

def test_the_skill_runs_only_the_bot_scripts_and_keeps_the_books_channel_rule():
    text = SKILL.read_text(encoding="utf-8")
    commands = re.findall(r"python3 -m (\S+)", text)
    assert commands and set(commands) <= {"bot.index_bot", "bot.discuss"}
    assert "bot.discuss" in commands
    for banned in ("sections/", ".epub", "coming soon", openclaw.BOOKS_CHANNEL_ID, openclaw.GUILD):
        assert banned not in text, banned
    assert not re.search(r"(?<![\w-])index/", text)  # the model name deepseek-index/... is not a path
    assert not re.search(r"\d{15,}", text)  # no Discord id of any kind
    status_calls = [line for line in text.splitlines() if "bot.index_bot status" in line]
    assert status_calls and all("--thread-name" in c and "--parent" in c for c in status_calls)
    discuss_calls = [line for line in text.splitlines() if re.search(r"bot\.discuss \w+ ", line)]
    assert discuss_calls and all("--thread-name" in c and "--parent" in c for c in discuss_calls)
    flat = " ".join(text.split())
    assert "answer nothing" in flat.casefold()
    assert f'"{UNKNOWN_BOOK_LINE}"' in flat


def test_the_skill_carries_on_only_on_exit_2_with_the_marker_line():
    text = SKILL.read_text(encoding="utf-8")
    rules = [" ".join(rule.split()) for rule in re.split(r"\n\s*- ", text)]
    carry_on = [rule for rule in rules if "carry on as usual" in rule]
    assert len(carry_on) == 1
    assert re.search(r"\bexits? 2\b", carry_on[0]) and f"`{NOT_A_BOOK_THREAD_LINE}`" in carry_on[0]
    assert NOT_A_BOOK_THREAD_LINE == "not a book thread"
    assert "'\\''" in text  # the rule for an apostrophe inside single quotes


def test_the_skill_relays_only_the_last_line_a_script_printed():
    # If exec merges stderr into what the agent sees, only the last line is the script's reply.
    flat = " ".join(SKILL.read_text(encoding="utf-8").split())
    assert "Reply with the last line the command printed, word for word" in flat
    assert "exits 0 or 3 and the last line it printed is another line" in flat
    assert "**3**: reply with the last line it printed, word for word" in flat
    assert "Exit 3: reply with the last line it printed." in flat
    assert "what it printed, word for word" not in flat
    assert "reply with the printed line." not in flat.split("### Answering")[1]


def test_the_skill_answers_from_what_read_printed_and_keeps_the_one_fallback_word_for_word():
    flat = " ".join(SKILL.read_text(encoding="utf-8").split())
    answering = flat.split("### Answering")[1].split("## Setup")[0]
    assert "answer from whatever the output holds that bears on the question" in answering
    assert "and stop after what you can cite: say nothing about what is missing" in answering
    assert f"only when the output holds nothing on the question, the whole answer after line 1 is exactly `{FALLBACK}`" in answering
    never = "Never reword that line (no \"isn't named\", \"not yet\" or \"so far\" of your own) and never hint that something comes later;"
    assert never in answering
    rejects = ("`check` rejects the fallback's words (\"so far\", \"what you've read\", \"not yet\", \"isn't named\", "
               "\"not named\") anywhere but in the exact fallback.")
    assert rejects in answering
    assert "write \"the book hasn't said\" only as a cited sentence" in answering
    rest = answering.replace(never, "").replace(rejects, "").replace(FALLBACK, "")
    for banned in ("isn't named", "not yet", "so far", "later"):
        assert banned not in rest, banned
    assert "read 'the captain'\\''s log'" in answering  # an apostrophe in the key words


def test_the_skill_confirms_the_position_then_recaps_from_what_recap_printed():
    text = SKILL.read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert '| "refresh me", "remind me what happened", "where were we?", `/recap` | `where`, then `recap`, then `check` |' in flat
    recapping = flat.split("### Recapping")[1].split("## Setup")[0]
    assert '"Last time: <section> · scene <k> — still there?"' in recapping
    assert re.search(r"python3 -m bot\.discuss recap --thread ", text)
    assert "line 1 is exactly the first line `recap` printed" in recapping
    assert "about 300 to 400 words" in recapping and "never write `[chapter 0]`" in recapping
    assert "never list the book's sections" in recapping
    assert "`place 'ch 9'`" in recapping and "`read --section" in recapping and "`read 'Kell'`" in recapping
    assert "story so far" not in flat.casefold()


def test_the_skill_sends_the_answer_to_check_only_through_the_quoted_heredoc():
    flat = " ".join(SKILL.read_text(encoding="utf-8").split())
    assert ("The answer reaches `check` only through this quoted heredoc: never through `printf`, "
            "`echo … | base64 -d`, any other pipe or a temporary file.") in flat


def quoted(text: str) -> str:
    """Reader text or a thread name in single quotes, the way bot/SKILL.md says to write it."""
    return "'" + text.replace("'", "'\\''") + "'"


def skill_line(start: str) -> str:
    return next(line.strip() for line in SKILL.read_text(encoding="utf-8").splitlines()
                if line.strip().startswith("cd ") and start in line)


def run_in_shell(line: str, data: Path, **values: str) -> tuple[int, str]:
    """Runs a command line from bot/SKILL.md through bash, against this checkout and `data`."""
    line = line.replace("cd ~/.openclaw/tools/scriptorium && python3",
                        f"cd {shlex.quote(str(REPO))} && {shlex.quote(sys.executable)}")
    line = line.replace("~/.openclaw/scriptorium", shlex.quote(str(data)))
    for placeholder, value in values.items():
        assert placeholder in line, placeholder
        line = line.replace(placeholder, value)
    done = subprocess.run(["bash", "-c", line], capture_output=True, text=True, timeout=60)
    return done.returncode, done.stdout.rstrip("\n")


def test_an_apostrophe_in_a_thread_name_or_a_description_survives_the_shell(data):
    name = "Ender's Game — Orson Scott Card"
    write_epub(data / BOOK / f"{BOOK}.epub", [("a.xhtml", "<p>x</p>")], title="Ender's Game",
               authors=["Orson Scott Card"])
    (data / BOOK / "status.json").write_text(json.dumps({"state": "ready"}), encoding="utf-8")
    thread = {"<this thread's id>": "999", "'<this thread's name>'": quoted(name), "<the parent channel's id>": "123"}
    assert run_in_shell(skill_line("bot.index_bot status"), data, **thread) == (0, "ready")
    description = "the bit where Tamsin's on the quillfeather bridge"
    find = {"set 'ch 6 · scene 3'": f"find {quoted(description)} --within {quoted('ch 1')}"}
    code, out = run_in_shell(skill_line("bot.discuss set"), data, **thread, **find)
    assert (code, out.splitlines()[0]) == (0, MATCHES_LINE)
    assert 'ch 1 · scene 1 — "Tamsin Rook crossed the quillfeather bridge at dusk."' in out
    other = {**thread, "'<this thread's name>'": quoted("Kell's Other Book")}
    assert run_in_shell(skill_line("bot.index_bot status"), data, **other) == (2, NOT_A_BOOK_THREAD_LINE)


def test_the_read_key_words_survive_the_shell_and_never_run(data, capsys):
    thread = {"<this thread's id>": THREAD, "'<this thread's name>'": quoted("Kell's Other Book"),
              "<the parent channel's id>": "123"}
    at = {"set 'ch 6 · scene 3'": "set 'ch 1 · scene 1'"}
    assert run_in_shell(skill_line("bot.discuss set"), data, **thread, **at)[0] == 0
    ran = [data / "ran-dollar", data / "ran-backtick", data / "ran-semicolon"]
    words = f"Tamsin and Kell's ship $(touch {ran[0]}) `touch {ran[1]}`; touch {ran[2]}"
    code, out = run_in_shell(skill_line("bot.discuss read"), data, **thread, **{"'<key words>'": quoted(words)})
    assert (code, out.splitlines()[0]) == (0, "Up to ch 1 · scene 1:")
    assert not any(path.exists() for path in ran)
    assert (code, out) == say(data, capsys, "read", words)[:2]


def test_read_takes_the_key_words_as_one_phrase_or_as_separate_words(data, capsys):
    say(data, capsys, "set", "ch 2 · scene 1")
    phrase = say(data, capsys, "read", "Tamsin's bridge")
    assert phrase[0] == 0 and "Tamsin" in phrase[1]
    assert phrase == say(data, capsys, "read", "Tamsin's", "bridge")


def test_an_unescaped_apostrophe_breaks_the_shell_without_the_marker(data):
    thread = {"<this thread's id>": "999", "'<this thread's name>'": "'Kell's ship'",
              "<the parent channel's id>": "123"}
    code, out = run_in_shell(skill_line("bot.index_bot status"), data, **thread)
    assert code == 2 and out != NOT_A_BOOK_THREAD_LINE


def run_script(module: str, *argv: str) -> tuple[int, str]:
    done = subprocess.run([sys.executable, "-m", module, *argv], cwd=REPO, capture_output=True, text=True,
                          timeout=60)
    assert "Traceback" not in done.stderr
    return done.returncode, done.stdout.rstrip("\n")


def test_discuss_as_a_process_exits_2_only_with_the_marker(data):
    assert run_script("bot.discuss", "where", "--thread", "999", "--data-dir", str(data)) == (
        2, NOT_A_BOOK_THREAD_LINE)
    for argv in (["where", "--thread", THREAD], ["where", "--thread", THREAD, "--help", "--data-dir", str(data)],
                 ["where", "--thread", "--data-dir", str(data)], ["where", "--thread", THREAD, "--data-dir"],
                 ["where", "--thread", THREAD, "--data-dir", "~nosuchuser-scriptorium/x"]):
        assert run_script("bot.discuss", *argv) == (3, USAGE_LINE), argv
