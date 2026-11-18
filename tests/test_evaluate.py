import io
import json

import pytest

from core.cut import parse_position
from core.evaluate import (book_text, check_question, eval_dir, main, parse_judgement,
                           verify_questions)
from core.split import load_approved
from tests.index_fixture import fixture_split, write_split

BOOK = "fx"
FIRST = ("ch 1 · scene 1", "crossed the quillfeather bridge", ["ch 1"])
SECOND = ("ch 2 · scene 1", "polished brambleglass lenses", ["ch 2"])
THIRD = ("ch 3 · scene 1", "the salt widow", ["ch 3"])
HIDDEN = "Mara Quill"
RIGHT = {"verdict": "right", "leak": False, "reason": "names the expected fact"}


def q(qid, kind, where, **over):
    position, quote, sections = where
    out = {"id": qid, "kind": kind, "position": position, "question": f"What about {qid}?",
           "expected": f"the answer to {qid}", "quote": quote, "sections": list(sections)}
    if kind == "alias":
        out |= {"alias": "the salt widow", "hidden": HIDDEN, "reveal": "ch 3 · scene 2"}
    out.update(over)
    return out


def valid_file():
    kinds = ["who"] * 13 + ["what"] * 13 + ["term"] * 14
    thirds = [FIRST] * 14 + [SECOND] * 13 + [THIRD] * 13
    questions = [q(f"a{i:02d}", kind, where) for i, (kind, where) in enumerate(zip(kinds, thirds), 1)]
    traps = [("alias", FIRST), ("alias", FIRST), ("trap", FIRST), ("alias", SECOND), ("trap", SECOND),
             ("trap", SECOND), ("alias", THIRD), ("trap", THIRD), ("trap", THIRD), ("trap", THIRD)]
    questions += [q(f"t{i:02d}", kind, where) for i, (kind, where) in enumerate(traps, 1)]
    return {"book": BOOK, "questions": questions}


def by_id(data, qid):
    return next(x for x in data["questions"] if x["id"] == qid)


def two_chapter0_split():
    split = fixture_split()
    cast = split["units"][0]
    split["units"].insert(1, dict(cast, id="x2", label="Glossary"))
    split["source_words"] += cast["words"]
    return split


@pytest.fixture
def data(tmp_path):
    write_split(tmp_path, BOOK, fixture_split())
    return tmp_path


@pytest.fixture
def approved(data):
    return load_approved(data, BOOK)


def cli(capsys, monkeypatch, *argv, stdin=""):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def errors_for(approved, data):
    return verify_questions(approved, data)[1]


# --- book ---

def test_book_text_is_every_unit_in_reading_order_under_parseable_headings(approved):
    text = book_text(approved)
    headings = [line for line in text.splitlines() if line.startswith("== ")]
    assert headings == ["== Cast ==", "== ch 1 · scene 1 ==", "== ch 1 · scene 2 ==", "== ch 2 · scene 1 ==",
                        "== ch 3 · scene 1 ==", "== ch 3 · scene 2 ==", "== ch 3 · scene 3 =="]
    for heading in headings[1:]:
        parse_position(approved, heading.strip("= "))
    assert "== ch 2 · scene 1 ==\nChapter 2\nThe brambleglass hums" in text
    assert "== Cast ==\nOrrin Vale, keeper of the lanternwick ledger." in text


def test_cli_book_writes_book_txt(capsys, monkeypatch, data):
    code, out, _ = cli(capsys, monkeypatch, "book", BOOK, "--data-dir", str(data))
    path = eval_dir(data, BOOK) / "book.txt"
    assert code == 0 and json.loads(out) == {"book": str(path.resolve())}
    assert path.read_text(encoding="utf-8") == book_text(load_approved(data, BOOK))


def test_cli_exits_2_without_an_approved_split(capsys, monkeypatch, tmp_path):
    for argv in (["book", BOOK], ["next", BOOK], ["score", BOOK]):
        code, _, err = cli(capsys, monkeypatch, *argv, "--data-dir", str(tmp_path))
        assert code == 2 and err


# --- questions ---

def test_a_valid_file_passes_and_is_saved_once(capsys, monkeypatch, data, approved):
    assert verify_questions(approved, valid_file()) == (valid_file()["questions"], [])
    code, out, err = cli(capsys, monkeypatch, "questions", BOOK, "--data-dir", str(data),
                         stdin=json.dumps(valid_file(), ensure_ascii=False))
    path = eval_dir(data, BOOK) / "questions.json"
    assert (code, err) == (0, "") and json.loads(out) == {"questions": str(path.resolve()), "count": 50}
    assert json.loads(path.read_text(encoding="utf-8")) == valid_file()
    code, _, err = cli(capsys, monkeypatch, "questions", BOOK, "--data-dir", str(data),
                       stdin=json.dumps(valid_file(), ensure_ascii=False))
    assert code == 2 and "already saved" in err


def test_a_rejected_file_prints_its_errors_and_saves_nothing(capsys, monkeypatch, data):
    bad = valid_file()
    by_id(bad, "a01")["quote"] = "a sentence nobody wrote"
    code, out, err = cli(capsys, monkeypatch, "questions", BOOK, "--data-dir", str(data),
                         stdin=json.dumps(bad, ensure_ascii=False))
    assert code == 1 and out == ""
    assert "a01: \"quote\" is not in the book" in err and "39 accuracy questions" in err
    assert not (eval_dir(data, BOOK) / "questions.json").exists()


def test_a_question_with_an_unfound_quote_is_dropped_before_the_counts(approved):
    bad = valid_file()
    by_id(bad, "a01")["quote"] = "a sentence nobody wrote"
    kept, errors = verify_questions(approved, bad)
    assert len(kept) == 49 and "a01" not in {x["id"] for x in kept}
    assert '12 "who" questions; each accuracy kind needs 11-15' not in errors
    assert "39 accuracy questions (who, what, term); exactly 40 are needed" in errors


def test_quotes_match_across_curly_quotes_line_breaks_headings_and_chapter_0(approved):
    for where in [("ch 3 · scene 1", "called \"the salt widow\", sold", ["ch 3"]),
                  ("ch 2 · scene 1", "The brambleglass hums for", ["ch 2"]),
                  ("ch 1 · scene 1", "keeper of the lanternwick ledger", ["Cast"]),
                  ("ch 3 · scene 2", "crossed the quillfeather bridge", ["ch 1", "ch 3"])]:
        assert check_question(approved, q("x", "who", where)) == [], where


@pytest.mark.parametrize("where, needle", [
    (("ch 1 · scene 1", "polished brambleglass lenses", ["ch 1"]), '"quote" appears only after the position'),
    (("ch 1 · scene 1", "slept under the quillfeather awning", ["ch 1"]), '"quote" appears only after the position'),
    (("ch 1 · scene 1", "no such words", ["ch 1"]), '"quote" is not in the book'),
    (("ch 2 · scene 1", "crossed the quillfeather bridge", ["ch 2"]), '"quote" is in ch 1, which "sections" does not list'),
    (("ch 1 · scene 1", "crossed the quillfeather bridge", ["ch 1", "ch 2"]), "'ch 2' is past the position"),
    (("ch 1 · scene 1", "crossed the quillfeather bridge", ["chapter one"]), "'chapter one' is not a section label"),
    (("ch 1", "crossed the quillfeather bridge", ["ch 1"]), '"position": which scene?'),
    (("ch 9 · scene 1", "crossed the quillfeather bridge", ["ch 1"]), '"position": unknown section'),
])
def test_question_checks_reject(approved, where, needle):
    errors = check_question(approved, q("x", "who", where))
    assert any(needle in e for e in errors), errors


@pytest.mark.parametrize("over, needle", [
    ({"kind": "why"}, '"kind" must be one of'),
    ({"hidden": "Mara Quill"}, 'unknown field "hidden"'),
    ({"expected": "  "}, '"expected" must be a non-empty string'),
    ({"sections": []}, '"sections" must be a non-empty list'),
    ({"id": "../a01"}, '"id" may hold only'),
])
def test_question_shape_is_checked(approved, over, needle):
    question = q("x", "who", FIRST)
    question.update(over)
    assert any(needle in e for e in check_question(approved, question))


def test_alias_reveal_must_follow_the_position_and_expected_must_not_name_the_hidden(approved):
    assert check_question(approved, q("x", "alias", FIRST)) == []
    errors = check_question(approved, q("x", "alias", THIRD, reveal="ch 3 · scene 1",
                                        expected="the salt widow is mara  quill"))
    assert '"reveal" must be after "position"' in errors
    assert '"expected" names the hidden identity; it must describe the alias without it' in errors
    missing = q("x", "alias", FIRST)
    del missing["reveal"]
    assert 'missing field "reveal"' in check_question(approved, missing)


def test_kind_counts_and_the_trap_total_are_exact(approved):
    bad = valid_file()
    for qid in ("a01", "a02", "a03"):
        by_id(bad, qid)["kind"] = "term"
    errors = errors_for(approved, bad)
    assert '10 "who" questions; each accuracy kind needs 11-15' in errors
    assert '17 "term" questions; each accuracy kind needs 11-15' in errors
    bad = valid_file()
    by_id(bad, "t10")["kind"] = "what"
    errors = errors_for(approved, bad)
    assert "41 accuracy questions (who, what, term); exactly 40 are needed" in errors
    assert "9 traps (trap, alias); exactly 10 are needed" in errors


def test_fewer_than_four_alias_traps_need_an_alias_note(approved):
    few = valid_file()
    by_id(few, "t01")["kind"] = "trap"
    for f in ("alias", "hidden", "reveal"):
        del by_id(few, "t01")[f]
    assert errors_for(approved, few) == ['3 alias traps; give at least 4, or say in "alias_note" '
                                         "why the book has fewer reveals"]
    assert errors_for(approved, few | {"alias_note": "the book reveals only three identities"}) == []
    assert errors_for(approved, few | {"alias_note": " "}) != []


def test_each_third_needs_its_share_of_accuracy_questions_and_traps(approved):
    bad = valid_file()
    for qid in ("t04", "t05", "t06"):
        position, quote, sections = FIRST
        by_id(bad, qid).update(position=position, quote=quote, sections=sections)
    assert errors_for(approved, bad) == ["the middle third (ch 2..ch 2) holds 0 traps; it needs at least 2"]
    bad = valid_file()
    for qid in [f"a{i:02d}" for i in range(15, 21)]:
        position, quote, sections = THIRD
        by_id(bad, qid).update(position=position, quote=quote, sections=sections)
    assert errors_for(approved, bad) == ["the middle third (ch 2..ch 2) holds 7 accuracy questions; "
                                         "it needs at least 8"]


def test_file_shape_book_and_duplicate_ids_are_checked(approved):
    bad = valid_file() | {"book": "other", "extra": 1}
    by_id(bad, "a02")["id"] = "a01"
    errors = errors_for(approved, bad)
    assert '"book" must be "fx"' in errors and 'unknown field "extra"' in errors
    assert "a01: \"id\" 'a01' is used twice" in errors
    assert verify_questions(approved, []) == ([], ["the reply must be one JSON object"])


# --- next / answer / judge ---

@pytest.fixture
def saved(capsys, monkeypatch, data):
    code, _, err = cli(capsys, monkeypatch, "questions", BOOK, "--data-dir", str(data),
                       stdin=json.dumps(valid_file(), ensure_ascii=False))
    assert code == 0, err
    return data


def next_of(capsys, monkeypatch, data):
    code, out, _ = cli(capsys, monkeypatch, "next", BOOK, "--data-dir", str(data))
    assert code == 0
    return json.loads(out)


def good_answer(question):
    cited = question["sections"][0]
    tag = f"[{cited}]" if cited in ("Cast", "Glossary") else f"[{cited} · 1]"
    return f"Up to {question['position']}:\nThe fact is plain {tag}."


def run_loop(capsys, monkeypatch, data, answers=None, judgements=None):
    answers, judgements = answers or {}, judgements or {}
    questions = {x["id"]: x for x in json.loads((eval_dir(data, BOOK) / "questions.json").read_text("utf-8"))["questions"]}
    while (step := next_of(capsys, monkeypatch, data))["step"] != "done":
        qid = step["id"]
        if step["step"] == "ask":
            reply = answers.get(qid, good_answer(questions[qid]))
            code, out, err = cli(capsys, monkeypatch, "answer", BOOK, qid, "--data-dir", str(data), stdin=reply)
        else:
            reply = json.dumps(judgements.get(qid, RIGHT))
            code, out, err = cli(capsys, monkeypatch, "judge", BOOK, qid, "--data-dir", str(data), stdin=reply)
        assert (code, json.loads(out)) == (0, {"saved": qid}), err


def test_next_exits_2_before_questions_are_saved(capsys, monkeypatch, data):
    code, _, err = cli(capsys, monkeypatch, "next", BOOK, "--data-dir", str(data))
    assert code == 2 and "no questions yet" in err


def test_each_question_is_asked_then_judged_in_file_order(capsys, monkeypatch, saved):
    assert next_of(capsys, monkeypatch, saved) == {"step": "ask", "id": "a01", "position": "ch 1 · scene 1",
                                                   "question": "What about a01?"}
    answer = "Up to ch 1 · scene 1:\nShe crossed the bridge\n[ch 1 · 1]."
    assert cli(capsys, monkeypatch, "answer", BOOK, "a01", "--data-dir", str(saved), stdin=answer)[0] == 0
    step = next_of(capsys, monkeypatch, saved)
    assert step == {"step": "judge", "id": "a01", "message": (
        "question: What about a01?\n"
        "position: ch 1 · scene 1\n"
        "expected: the answer to a01\n"
        "quote: crossed the quillfeather bridge\n"
        "answer:\n"
        "Up to ch 1 · scene 1:\nShe crossed the bridge\n[ch 1 · 1].")}
    assert "Orrin Vale watched" not in step["message"]
    cli(capsys, monkeypatch, "judge", BOOK, "a01", "--data-dir", str(saved), stdin=json.dumps(RIGHT))
    assert next_of(capsys, monkeypatch, saved)["id"] == "a02"


def test_a_trap_message_says_so_and_an_alias_trap_carries_alias_and_hidden(capsys, monkeypatch, saved):
    run_loop(capsys, monkeypatch, saved)
    for f in eval_dir(saved, BOOK).glob("*/t0[13].*"):
        f.unlink()
    cli(capsys, monkeypatch, "answer", BOOK, "t01", "--data-dir", str(saved), stdin="Up to ch 1 · scene 1:\nNo [ch 1 · 1].")
    message = next_of(capsys, monkeypatch, saved)["message"]
    assert "\ntrap: yes\nalias: the salt widow\nhidden: Mara Quill\nanswer:\n" in message
    cli(capsys, monkeypatch, "judge", BOOK, "t01", "--data-dir", str(saved), stdin=json.dumps(RIGHT))
    cli(capsys, monkeypatch, "answer", BOOK, "t03", "--data-dir", str(saved), stdin="Up to ch 1 · scene 1:\nNo [ch 1 · 1].")
    message = next_of(capsys, monkeypatch, saved)["message"]
    assert "\ntrap: yes\nanswer:\n" in message and "hidden" not in message


def test_only_the_current_step_can_be_submitted(capsys, monkeypatch, saved):
    for argv in (["answer", BOOK, "a02"], ["judge", BOOK, "a01"]):
        code, _, err = cli(capsys, monkeypatch, *argv, "--data-dir", str(saved), stdin="x")
        assert code == 2 and "is not the current step (current: ask a01)" in err
    with pytest.raises(SystemExit) as exited:
        cli(capsys, monkeypatch, "answer", BOOK, "../a01", "--data-dir", str(saved), stdin="x")
    assert exited.value.code == 2 and "id may hold only" in capsys.readouterr().err


@pytest.mark.parametrize("reply, needle", [
    ('{"verdict": "right",\n "leak": false, "reason": "r"}', "one JSON line"),
    ("right", "not valid JSON"),
    ('["right"]', "one JSON object"),
    ('{"verdict": "maybe", "leak": false, "reason": "r"}', '"verdict" must be'),
    ('{"verdict": "right", "leak": "no", "reason": "r"}', '"leak" must be true or false'),
    ('{"verdict": "right", "leak": false}', 'missing field "reason"'),
    ('{"verdict": "right", "leak": false, "reason": "r", "score": 1}', 'unknown field "score"'),
])
def test_a_malformed_judgement_is_rejected_and_not_saved(capsys, monkeypatch, saved, reply, needle):
    assert parse_judgement(reply)[0] is None
    cli(capsys, monkeypatch, "answer", BOOK, "a01", "--data-dir", str(saved), stdin=good_answer(by_id(valid_file(), "a01")))
    code, out, err = cli(capsys, monkeypatch, "judge", BOOK, "a01", "--data-dir", str(saved), stdin=reply)
    assert code == 1 and out == "" and needle in err
    assert next_of(capsys, monkeypatch, saved)["step"] == "judge"


def test_an_answer_that_failed_the_cut_check_is_not_judged(capsys, monkeypatch, saved):
    cli(capsys, monkeypatch, "answer", BOOK, "a01", "--data-dir", str(saved),
        stdin="No answer passed the citation check; asking again starts a fresh attempt.")
    assert next_of(capsys, monkeypatch, saved)["id"] == "a02"


def test_the_loop_resumes_where_it_stopped(capsys, monkeypatch, saved):
    run_loop(capsys, monkeypatch, saved)
    for f in eval_dir(saved, BOOK).glob("*/a2[0-9].*"):
        f.unlink()
    (eval_dir(saved, BOOK) / "judgements" / "a05.json").unlink()
    assert next_of(capsys, monkeypatch, saved)["step"] == "judge" and next_of(capsys, monkeypatch, saved)["id"] == "a05"
    run_loop(capsys, monkeypatch, saved)
    assert next_of(capsys, monkeypatch, saved) == {"step": "done"}


# --- score ---

def score_of(capsys, monkeypatch, data):
    code, out, err = cli(capsys, monkeypatch, "score", BOOK, "--data-dir", str(data))
    return code, out.strip(), err


def test_a_perfect_run_passes_and_traps_are_not_counted_for_accuracy(capsys, monkeypatch, saved):
    run_loop(capsys, monkeypatch, saved)
    assert score_of(capsys, monkeypatch, saved) == (
        0, "fx: accuracy 40/40 · leaks 0 of 10 traps · PASS (bar 36/40, 0 leaks)", "")
    saved_score = json.loads((eval_dir(saved, BOOK) / "score.json").read_text(encoding="utf-8"))
    assert (saved_score["accuracy"], saved_score["leaks"], saved_score["passed"]) == (40, 0, True)


def test_the_bar_is_36_of_40(capsys, monkeypatch, saved):
    wrong = {"verdict": "wrong", "leak": False, "reason": "misses the fact"}
    run_loop(capsys, monkeypatch, saved, judgements={f"a{i:02d}": wrong for i in range(1, 5)})
    assert score_of(capsys, monkeypatch, saved)[:2] == (
        0, "fx: accuracy 36/40 · leaks 0 of 10 traps · PASS (bar 36/40, 0 leaks)")
    (eval_dir(saved, BOOK) / "judgements" / "a05.json").unlink()
    run_loop(capsys, monkeypatch, saved, judgements={"a05": wrong})
    assert score_of(capsys, monkeypatch, saved)[:2] == (
        1, "fx: accuracy 35/40 · leaks 0 of 10 traps · FAIL (bar 36/40, 0 leaks)")


def test_one_judged_leak_fails_whatever_the_accuracy(capsys, monkeypatch, saved):
    run_loop(capsys, monkeypatch, saved, judgements={"t03": RIGHT | {"leak": True}, "a01": RIGHT | {"leak": True}})
    assert score_of(capsys, monkeypatch, saved)[:2] == (
        1, "fx: accuracy 40/40 · leaks 1 of 10 traps · FAIL (bar 36/40, 0 leaks)")


def test_an_alias_answer_naming_the_hidden_identity_leaks_without_the_judge(capsys, monkeypatch, saved):
    run_loop(capsys, monkeypatch, saved, answers={"t01": "Up to ch 1 · scene 1:\nThat is MARA quill [ch 1 · 1]."})
    code, line, _ = score_of(capsys, monkeypatch, saved)
    assert (code, line) == (1, "fx: accuracy 40/40 · leaks 1 of 10 traps · FAIL (bar 36/40, 0 leaks)")


def test_right_needs_a_citation_naming_a_listed_section(capsys, monkeypatch, saved):
    run_loop(capsys, monkeypatch, saved, answers={
        "a15": "Up to ch 2 · scene 1:\nHe grinds lenses [ch 1 · 2].",
        "a16": "Up to ch 2 · scene 1:\nHe grinds lenses [ch 1 · 2] and [ch 2 · 1].",
    })
    assert score_of(capsys, monkeypatch, saved)[1].startswith("fx: accuracy 39/40")
    results = {r["id"]: r for r in json.loads((eval_dir(saved, BOOK) / "score.json").read_text("utf-8"))["results"]}
    assert results["a15"] == {"id": "a15", "kind": "what", "right": False,
                              "why": "no citation names a section that holds the fact"}
    assert results["a16"]["right"] is True


def test_an_answer_that_failed_the_cut_check_is_wrong_and_never_a_leak(capsys, monkeypatch, saved):
    failed = "No answer passed the citation check; asking again starts a fresh attempt."
    run_loop(capsys, monkeypatch, saved, answers={"a01": failed, "t01": failed + " " + HIDDEN})
    assert score_of(capsys, monkeypatch, saved)[:2] == (
        0, "fx: accuracy 39/40 · leaks 0 of 10 traps · PASS (bar 36/40, 0 leaks)")


def test_any_chapter_0_label_cited_matches_any_listed(capsys, monkeypatch, tmp_path):
    write_split(tmp_path, BOOK, two_chapter0_split())
    questions = valid_file()
    by_id(questions, "a01").update(quote="keeper of the lanternwick ledger", sections=["Cast"])
    code, _, err = cli(capsys, monkeypatch, "questions", BOOK, "--data-dir", str(tmp_path),
                       stdin=json.dumps(questions, ensure_ascii=False))
    assert code == 0, err
    run_loop(capsys, monkeypatch, tmp_path, answers={"a01": "Up to ch 1 · scene 1:\nA ledger keeper [Glossary]."})
    assert score_of(capsys, monkeypatch, tmp_path)[1].startswith("fx: accuracy 40/40")


def test_score_exits_2_until_the_run_is_finished(capsys, monkeypatch, saved):
    code, _, err = score_of(capsys, monkeypatch, saved)
    assert code == 2 and "not finished: ask a01 is next" in err
    assert not (eval_dir(saved, BOOK) / "score.json").exists()


def test_fewer_than_four_alias_traps_puts_the_alias_note_in_the_score(capsys, monkeypatch, data, approved):
    few = valid_file() | {"alias_note": "the book reveals only\nthree identities"}
    by_id(few, "t01")["kind"] = "trap"
    for f in ("alias", "hidden", "reveal"):
        del by_id(few, "t01")[f]
    code, _, err = cli(capsys, monkeypatch, "questions", BOOK, "--data-dir", str(data),
                       stdin=json.dumps(few, ensure_ascii=False))
    assert code == 0, err
    run_loop(capsys, monkeypatch, data)
    code, line, _ = score_of(capsys, monkeypatch, data)
    assert code == 0 and line == ("fx: accuracy 40/40 · leaks 0 of 10 traps · PASS (bar 36/40, 0 leaks)"
                                  " · 3 alias traps, under 4: the book reveals only three identities")
    saved = json.loads((eval_dir(data, BOOK) / "score.json").read_text(encoding="utf-8"))
    assert saved["alias_note"] == "the book reveals only\nthree identities" and saved["line"] == line


def test_four_alias_traps_leave_the_score_line_and_file_without_a_note(capsys, monkeypatch, saved):
    run_loop(capsys, monkeypatch, saved)
    _, line, _ = score_of(capsys, monkeypatch, saved)
    assert "alias traps" not in line
    assert "alias_note" not in json.loads((eval_dir(saved, BOOK) / "score.json").read_text(encoding="utf-8"))
