import json
from typing import Any

import pytest

from core import extract
from core.brief import Position
from core.extract import Outcome, main, next_step, read_outputs, run, slots, submit
from core.split import SplitNotApproved, load_approved
from tests.index_fixture import CAST, StubWorker, fixture_split, valid_reply, write_split

BOOK = "fx"
INSTRUCTIONS = "# Worker instructions\n\nReply with one JSON object."
BAD = "this is not json"
ORDER = ["s00", "s01", "s02", "s03"]


@pytest.fixture
def data(tmp_path):
    write_split(tmp_path, BOOK, fixture_split())
    return tmp_path


def sections(data):
    return data / BOOK / "sections"


def test_one_output_per_slot_in_reading_order_chapter_0_first(data):
    stub = StubWorker()
    assert run(data, BOOK, INSTRUCTIONS, stub) == 0
    assert [s.slot.id for s in stub.steps] == ORDER
    assert all(s.attempt == 1 for s in stub.steps)
    assert sorted(p.name for p in sections(data).iterdir()) == [f"{i}.json" for i in ORDER]
    s00 = json.loads((sections(data) / "s00.json").read_text(encoding="utf-8"))
    assert s00["section"] == "chapter 0" and len(s00["scenes"]) == 1
    s03 = json.loads((sections(data) / "s03.json").read_text(encoding="utf-8"))
    assert s03 == valid_reply(stub.steps[3])


def test_chapter_0_seeds_the_section_1_brief(data):
    stub = StubWorker()
    run(data, BOOK, INSTRUCTIONS, stub)
    assert [c["name"] for c in stub.steps[1].brief["characters"]] == ["Orrin Vale"]
    assert stub.steps[0].brief["characters"] == []


def test_prompts_hold_no_future_text_or_markers(data):
    stub = StubWorker()
    run(data, BOOK, INSTRUCTIONS, stub)
    markers = [marker for _, marker in CAST.values()]
    for i, step in enumerate(stub.steps):
        assert step.prompt.startswith(INSTRUCTIONS)
        for later in markers[i + 1:]:
            assert later not in step.prompt, (step.slot.id, later)
        assert markers[i] in step.prompt


def test_prompt_layout(data):
    step = next_step(data, BOOK, INSTRUCTIONS)
    assert step is not None
    assert "## Your section\n\nsection: \"chapter 0\"\nscenes: 1" in step.prompt
    assert "## Section text\n\n### Scene 1\n\n#### Cast\n\nOrrin Vale, keeper" in step.prompt
    assert "## Your previous reply was rejected" not in step.prompt
    stub = StubWorker()
    run(data, BOOK, INSTRUCTIONS, stub)
    ch2 = stub.steps[2].prompt
    assert "## Brief\n\n" in ch2 and "**Tamsin Rook**" in ch2
    assert "## Section text\n\nChapter 2\n\nThe brambleglass hums" in ch2
    assert "### Scene 1\n\nCorvin Ashby polished" in ch2


def test_reject_then_accept_saves_the_rejection_and_retries_with_its_errors(data):
    stub = StubWorker({("s01", 1): BAD})
    assert run(data, BOOK, INSTRUCTIONS, stub) == 0
    assert [(s.slot.id, s.attempt) for s in stub.steps] == [("s00", 1), ("s01", 1), ("s01", 2), ("s02", 1), ("s03", 1)]
    assert (sections(data) / "s01.rejected-1.txt").read_text(encoding="utf-8") == BAD
    errors = (sections(data) / "s01.rejected-1.errors.txt").read_text(encoding="utf-8").splitlines()
    assert errors and "not valid JSON" in errors[0]
    retry = stub.steps[2].prompt
    assert "## Your previous reply was rejected\n\n- " + errors[0] in retry
    assert (sections(data) / "s01.json").exists()


def test_reject_twice_stops_with_both_saved_and_no_later_slot_called(data):
    stub = StubWorker({("s02", 1): BAD, ("s02", 2): "{}"})
    assert run(data, BOOK, INSTRUCTIONS, stub) == 1
    assert [s.slot.id for s in stub.steps] == ["s00", "s01", "s02", "s02"]
    names = sorted(p.name for p in sections(data).iterdir())
    assert names == ["s00.json", "s01.json", "s02.rejected-1.errors.txt", "s02.rejected-1.txt",
                     "s02.rejected-2.errors.txt", "s02.rejected-2.txt"]
    assert (sections(data) / "s02.rejected-2.txt").read_text(encoding="utf-8") == "{}"


def test_rerun_resumes_at_first_missing_slot_and_a_stop_starts_a_fresh_pair(data):
    run(data, BOOK, INSTRUCTIONS, StubWorker({("s02", 1): BAD, ("s02", 2): BAD}))
    stub = StubWorker()
    assert run(data, BOOK, INSTRUCTIONS, stub) == 0
    assert [(s.slot.id, s.attempt) for s in stub.steps] == [("s02", 3), ("s03", 1)]
    assert "## Your previous reply was rejected" not in stub.steps[0].prompt
    assert (sections(data) / "s02.rejected-2.txt").exists()


def test_warnings_go_to_a_log_and_do_not_gate(data):
    def model(step):
        reply = valid_reply(step)
        if step.slot.id == "s01":
            reply["scenes"][1]["characters"] = []
        return json.dumps(reply)

    assert run(data, BOOK, INSTRUCTIONS, model) == 0
    assert (sections(data) / "s01.warnings.log").read_text(encoding="utf-8") == "scene 2: no characters\n"
    assert not (sections(data) / "s02.warnings.log").exists()


def test_unapproved_or_missing_split_raises_before_any_model_call(tmp_path):
    stub = StubWorker()
    with pytest.raises(FileNotFoundError):
        run(tmp_path, BOOK, INSTRUCTIONS, stub)
    write_split(tmp_path, BOOK, {**fixture_split(), "approved": False})
    with pytest.raises(SplitNotApproved):
        run(tmp_path, BOOK, INSTRUCTIONS, stub)
    assert stub.steps == []


def test_read_outputs_never_opens_a_file_past_the_position(data):
    run(data, BOOK, INSTRUCTIONS, StubWorker())
    (sections(data) / "s03.json").write_text("{ corrupt", encoding="utf-8")
    approved = load_approved(data, BOOK)
    chapter0, recs = read_outputs(data, BOOK, approved, Position(2, 1))
    assert chapter0 is not None and chapter0["section"] == "chapter 0"
    assert [(o, label) for o, label, _ in recs] == [(1, "ch 1"), (2, "ch 2")]
    assert read_outputs(data, BOOK, approved, Position(0, 0)) == (None, [])


def test_submit_refuses_a_slot_that_is_not_current(data):
    with pytest.raises(ValueError, match="s01 is not the current slot"):
        submit(data, BOOK, "s01", "{}")
    assert not sections(data).exists()


def test_submit_result_carries_errors_and_outcome(data):
    assert submit(data, BOOK, "s00", BAD).outcome is Outcome.RETRY
    result = submit(data, BOOK, "s00", BAD)
    assert result.outcome is Outcome.STOPPED and result.errors and result.warnings == []


def test_slot_scene_texts_fold_the_heading_into_scene_1(data):
    by_id = {s.id: s for s in slots(load_approved(data, BOOK))}
    assert by_id["s02"].scene_texts[0].startswith("Chapter 2\nThe brambleglass hums")
    assert by_id["s03"].scene_texts[1] == "Ilse Marrow counted thornwick knots."
    assert by_id["s00"].scene_texts == ["Cast\nOrrin Vale, keeper of the lanternwick ledger."]


def cli(capsys, *argv) -> tuple[int, Any, str]:
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, (json.loads(out) if out.strip() else None), err


def test_cli_round_trip(data, capsys, tmp_path_factory):
    instr = tmp_path_factory.mktemp("p") / "worker-instructions.md"
    instr.write_text(INSTRUCTIONS, encoding="utf-8")
    common = ["--data-dir", str(data)]

    code, line, _ = cli(capsys, "next", BOOK, "--instructions", str(instr), *common)
    assert code == 0
    assert set(line) == {"slot", "section", "attempt", "prompt", "reply"}
    assert (line["slot"], line["section"], line["attempt"]) == ("s00", "chapter 0", 1)
    prompt, reply = data / BOOK / "work" / "s00.prompt.md", data / BOOK / "work" / "s00.reply.json"
    assert line["prompt"] == str(prompt.resolve()) and line["reply"] == str(reply.resolve())
    assert prompt.read_text(encoding="utf-8").startswith(INSTRUCTIONS)

    reply.write_text(BAD, encoding="utf-8")
    code, line, _ = cli(capsys, "submit", BOOK, "s00", *common)
    assert code == 3 and line["outcome"] == "retry" and line["errors"] and line["warnings"] == 0
    assert not reply.exists()

    reply.write_text("stale", encoding="utf-8")
    code, line, _ = cli(capsys, "next", BOOK, "--instructions", str(instr), *common)
    assert code == 0 and line["attempt"] == 2 and not reply.exists()
    assert "## Your previous reply was rejected" in prompt.read_text(encoding="utf-8")

    reply.write_text(BAD, encoding="utf-8")
    code, line, _ = cli(capsys, "submit", BOOK, "s00", *common)
    assert code == 1 and line["outcome"] == "stopped"

    for _ in ORDER:
        code, line, _ = cli(capsys, "next", BOOK, "--instructions", str(instr), *common)
        step = next_step(data, BOOK, INSTRUCTIONS)
        assert step is not None
        reply = data / BOOK / "work" / f"{line['slot']}.reply.json"
        reply.write_text(json.dumps(valid_reply(step)), encoding="utf-8")
        code, line, _ = cli(capsys, "submit", BOOK, line["slot"], *common)
        assert code == 0 and line == {"outcome": "accepted", "errors": [], "warnings": 0}
    assert cli(capsys, "next", BOOK, "--instructions", str(instr), *common)[:2] == (0, {"done": True})


def test_cli_exit_2_on_missing_input(data, capsys, tmp_path):
    common = ["--data-dir", str(data)]
    code, _, err = cli(capsys, "next", BOOK, "--instructions", str(tmp_path / "none.md"), *common)
    assert code == 2 and "no instructions file" in err
    code, _, err = cli(capsys, "submit", BOOK, "s00", *common)
    assert code == 2 and "no reply" in err
    reply = data / BOOK / "work" / "s01.reply.json"
    reply.parent.mkdir(parents=True)
    reply.write_text("{}", encoding="utf-8")
    code, _, err = cli(capsys, "submit", BOOK, "s01", *common)
    assert code == 2 and "not the current slot" in err and reply.exists()
    code, _, err = cli(capsys, "next", "nobook", "--instructions", str(tmp_path / "none.md"), *common)
    assert code == 2
    (tmp_path / "i.md").write_text("x", encoding="utf-8")
    code, _, err = cli(capsys, "next", "nobook", "--instructions", str(tmp_path / "i.md"), *common)
    assert code == 2 and "split.json" in err
    code, _, err = cli(capsys, "submit", "nobook", "s00", *common)
    assert code == 2 and "split.json" in err


def test_default_instructions_path_is_the_shared_prompts_file():
    assert extract.INSTRUCTIONS.parts[-2:] == ("prompts", "worker-instructions.md")
