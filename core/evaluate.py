"""The evaluator's code half: the labelled book, the question-file check, the ask/judge loop
state and the score. No model is called here; the evaluate-book skill runs the agents.

    python -m core.evaluate book <book> [--data-dir DIR]
    python -m core.evaluate questions <book> [--data-dir DIR]     (the writer's reply on stdin)
    python -m core.evaluate next <book> [--data-dir DIR]
    python -m core.evaluate answer <book> <id> [--data-dir DIR]   (what /ask-book replied, on stdin)
    python -m core.evaluate judge <book> <id> [--data-dir DIR]    (the judge's reply, on stdin)
    python -m core.evaluate score <book> [--data-dir DIR]
    python -m core.evaluate recap-next <book> [--data-dir DIR]
    python -m core.evaluate recap-judge <book> <id> [--data-dir DIR]   (the recap judge's reply, on stdin)
    python -m core.evaluate recap-score <book> [--data-dir DIR]

State lives under <data-dir>/<book>/eval/: book.txt, questions.json, answers/<id>.txt,
judgements/<id>.json, score.json. A step whose file exists is done, so `next` resumes.

The recap pass is kept apart from the questions: ids r1..r3 at recap_positions, with
recaps/<id>.md (saved by the bot driver through save_recap), recap-fence/<id>.json,
recap-judgements/<id>.json and recap-score.json. `recap-next` prints {"step": "ask", "id",
"position"}, {"step": "judge", "id", "message"} or {"step": "done"}; `recap-judge` prints
{"saved": <id>}; `recap-score` prints one line and exits 0 at no leak, no later citation and
no fence breach, else 1.

`book` prints {"book": <abs path>}. `questions` saves the file and prints {"questions": <abs
path>, "count": n}, or prints one error per line to stderr. `next` prints one JSON line:
{"step": "ask", "id", "position", "question"}, {"step": "judge", "id", "message"} or
{"step": "done"}. `answer` and `judge` print {"saved": <id>}. `score` prints one line.

Exit codes: questions and judge 0 saved, 1 rejected (nothing saved); score 0 pass, 1 fail;
book, next, answer 0. Every command exits 2 on a missing or unapproved split, a missing
questions file, questions already saved, an id that is not the current step, or (score) an
unfinished run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from core.brief import Position, build_recap, render_recap
from core.cut import check_answer, citations, parse_position, position_line
from core.extract import _write_text, read_outputs, slots
from core.split import DEFAULT_DATA_DIR, SplitNotApproved, load_approved
from core.validate import normalise, parse_reply

ACCURACY_KINDS = ("who", "what", "term")
TRAP_KINDS = ("trap", "alias")
ACCURACY_TOTAL = 40
KIND_MIN = 11
KIND_MAX = 15
TRAP_TOTAL = 10
ALIAS_MIN = 4
THIRD_ACCURACY_MIN = 8
THIRD_TRAP_MIN = 2
PASS_ACCURACY = 36
PASS_LEAKS = 0

QUESTION_FIELDS = ("id", "kind", "position", "question", "expected", "quote", "sections")
ALIAS_FIELDS = ("alias", "hidden", "reveal")
TOP_FIELDS = ("book", "questions")
TOP_OPTIONAL = ("alias_note",)
JUDGEMENT_FIELDS = ("verdict", "leak", "reason")
VERDICTS = ("right", "wrong")
RECAP_JUDGEMENT_FIELDS = ("leak", "reason")

_ID = re.compile(r"[A-Za-z0-9_-]+\Z")
_THIRDS = ("early", "middle", "late")


def eval_dir(data_dir: str | os.PathLike[str], book: str) -> Path:
    return Path(data_dir) / book / "eval"


def _questions_path(data_dir: str | os.PathLike[str], book: str) -> Path:
    return eval_dir(data_dir, book) / "questions.json"


def _answer_path(data_dir: str | os.PathLike[str], book: str, qid: str) -> Path:
    return eval_dir(data_dir, book) / "answers" / f"{qid}.txt"


def _judgement_path(data_dir: str | os.PathLike[str], book: str, qid: str) -> Path:
    return eval_dir(data_dir, book) / "judgements" / f"{qid}.json"


def _recap_path(data_dir: str | os.PathLike[str], book: str, rid: str) -> Path:
    return eval_dir(data_dir, book) / "recaps" / f"{rid}.md"


def _recap_judgement_path(data_dir: str | os.PathLike[str], book: str, rid: str) -> Path:
    return eval_dir(data_dir, book) / "recap-judgements" / f"{rid}.json"


def recap_fence_path(data_dir: str | os.PathLike[str], book: str, rid: str) -> Path:
    """Where the bot's fence record for one recap lives; same shape as a question's."""
    return eval_dir(data_dir, book) / "recap-fence" / f"{rid}.json"


def _labels(approved: dict) -> dict[int, str]:
    """Story section label by order."""
    return {s.order: s.section for s in slots(approved) if not s.chapter0}


def _chapter0_labels(approved: dict) -> set[str]:
    return {u["label"] for u in approved["chapter0"]}


def _position_text(approved: dict, at: Position) -> str:
    return f"{_labels(approved)[at.order]} · scene {at.scene}"


def _one_line(text: str) -> str:
    return " ".join(text.split())


def book_text(approved: dict) -> str:
    """The whole book in reading order, as the question writer reads it.

    Chapter-0 units are headed `== <label> ==`, story scenes `== <label> · scene <k> ==`;
    the text under a story heading is exactly what the quote check searches.
    """
    blocks: list[str] = []
    for slot in slots(approved):
        if slot.chapter0:
            blocks += [f"== {label} ==\n" + "\n".join(paras) for label, paras in slot.parts]
        else:
            blocks += [f"== {slot.section} · scene {k} ==\n{text}" for k, text in enumerate(slot.scene_texts, 1)]
    return "\n\n".join(blocks) + "\n"


def _where_quote_is(approved: dict, quote: str, at: Position) -> tuple[set[str], bool]:
    """(labels of the sections holding the quote at or before `at`, whether it occurs later).

    A chapter-0 hit is reported as every chapter-0 label: chapter 0 is one merged scene.
    """
    needle = normalise(quote)
    before: set[str] = set()
    later = False
    for slot in slots(approved):
        for k, text in enumerate(slot.scene_texts, 1):
            if needle not in normalise(text):
                continue
            if slot.chapter0:
                before |= _chapter0_labels(approved)
            elif (slot.order, k) <= (at.order, at.scene):
                before.add(slot.section)
            else:
                later = True
    return before, later


def check_question(approved: dict, question: object) -> list[str]:
    """The errors in one question of the question file; empty means it is usable."""
    if not isinstance(question, dict):
        return ["must be an object"]
    kind = question.get("kind")
    required = QUESTION_FIELDS + (ALIAS_FIELDS if kind == "alias" else ())
    errors = [f'missing field "{f}"' for f in required if f not in question]
    errors += [f'unknown field "{k}"' for k in question if k not in required]
    if "kind" in question and kind not in ACCURACY_KINDS + TRAP_KINDS:
        errors.append(f'"kind" must be one of {", ".join(ACCURACY_KINDS + TRAP_KINDS)}')
    for field in required:
        if field == "sections" or field == "kind" or field not in question:
            continue
        value = question[field]
        if not isinstance(value, str) or not value.strip():
            errors.append(f'"{field}" must be a non-empty string')
    listed = question.get("sections")
    if "sections" in question and (not isinstance(listed, list) or not listed
                                   or not all(isinstance(s, str) for s in listed)):
        errors.append('"sections" must be a non-empty list of section labels')
    if isinstance(question.get("id"), str) and question["id"] and not _ID.match(question["id"]):
        errors.append('"id" may hold only letters, digits, "-" and "_"')
    if errors:
        return errors

    sections: list[str] = question["sections"]
    try:
        at = parse_position(approved, question["position"])
    except ValueError as e:
        return [f'"position": {e}']
    labels = _labels(approved)
    chapter0 = _chapter0_labels(approved)
    order_of = {label: order for order, label in labels.items()}
    for label in dict.fromkeys(sections):
        if label in chapter0:
            continue
        if label not in order_of:
            errors.append(f'"sections": {label!r} is not a section label as the book prints it')
        elif order_of[label] > at.order:
            errors.append(f'"sections": {label!r} is past the position')

    found, later = _where_quote_is(approved, question["quote"], at)
    if not found:
        errors.append('"quote" appears only after the position; the fact must be knowable there' if later
                      else '"quote" is not in the book; copy one contiguous stretch verbatim')
    elif not found & set(sections):
        errors.append(f'"quote" is in {", ".join(sorted(found))}, which "sections" does not list')

    if kind == "alias":
        try:
            reveal = parse_position(approved, question["reveal"])
        except ValueError as e:
            errors.append(f'"reveal": {e}')
        else:
            if reveal <= at:
                errors.append('"reveal" must be after "position"')
        if normalise(question["hidden"]).casefold() in normalise(question["expected"]).casefold():
            errors.append('"expected" names the hidden identity; it must describe the alias without it')
    return errors


def _third(approved: dict, order: int) -> int:
    orders = sorted(_labels(approved))
    return orders.index(order) * len(_THIRDS) // len(orders)


def _spread_errors(approved: dict, questions: list[dict]) -> list[str]:
    labels = _labels(approved)
    accuracy = [0] * len(_THIRDS)
    traps = [0] * len(_THIRDS)
    for q in questions:
        third = _third(approved, parse_position(approved, q["position"]).order)
        (accuracy if q["kind"] in ACCURACY_KINDS else traps)[third] += 1
    errors = []
    for i, name in enumerate(_THIRDS):
        members = [labels[o] for o in sorted(labels) if _third(approved, o) == i]
        span = f"{members[0]}..{members[-1]}" if members else "no sections"
        if accuracy[i] < THIRD_ACCURACY_MIN:
            errors.append(f"the {name} third ({span}) holds {accuracy[i]} accuracy questions; "
                          f"it needs at least {THIRD_ACCURACY_MIN}")
        if traps[i] < THIRD_TRAP_MIN:
            errors.append(f"the {name} third ({span}) holds {traps[i]} traps; it needs at least {THIRD_TRAP_MIN}")
    return errors


def verify_questions(approved: dict, data: object) -> tuple[list[dict], list[str]]:
    """(the usable questions, every error). The file is acceptable only when errors is empty.

    A question with any error is dropped before the counts and the spread are checked, so
    the count errors describe what would be kept.
    """
    if not isinstance(data, dict):
        return [], ["the reply must be one JSON object"]
    errors = [f'missing field "{f}"' for f in TOP_FIELDS if f not in data]
    errors += [f'unknown field "{k}"' for k in data if k not in TOP_FIELDS + TOP_OPTIONAL]
    if "book" in data and data["book"] != approved["book"]:
        errors.append(f'"book" must be {json.dumps(approved["book"], ensure_ascii=False)}')
    note = data.get("alias_note")
    if "alias_note" in data and not isinstance(note, str):
        errors.append('"alias_note" must be a string')
    raw = data.get("questions")
    if not isinstance(raw, list):
        return [], errors + ['"questions" must be a list']

    kept: list[dict] = []
    seen: set[str] = set()
    for i, q in enumerate(raw):
        qid = q.get("id") if isinstance(q, dict) else None
        where = f"{qid}: " if isinstance(qid, str) and qid else f"questions[{i}]: "
        problems = check_question(approved, q)
        if not problems and qid in seen:
            problems = [f'"id" {qid!r} is used twice']
        if problems:
            errors += [where + p for p in problems]
            continue
        seen.add(q["id"])
        kept.append(q)

    count = {k: sum(q["kind"] == k for q in kept) for k in ACCURACY_KINDS + TRAP_KINDS}
    for kind in ACCURACY_KINDS:
        if not KIND_MIN <= count[kind] <= KIND_MAX:
            errors.append(f'{count[kind]} "{kind}" questions; each accuracy kind needs {KIND_MIN}-{KIND_MAX}')
    accuracy = sum(count[k] for k in ACCURACY_KINDS)
    if accuracy != ACCURACY_TOTAL:
        errors.append(f"{accuracy} accuracy questions (who, what, term); exactly {ACCURACY_TOTAL} are needed")
    traps = sum(count[k] for k in TRAP_KINDS)
    if traps != TRAP_TOTAL:
        errors.append(f"{traps} traps (trap, alias); exactly {TRAP_TOTAL} are needed")
    if count["alias"] < ALIAS_MIN and not (isinstance(note, str) and note.strip()):
        errors.append(f'{count["alias"]} alias traps; give at least {ALIAS_MIN}, or say in "alias_note" '
                      "why the book has fewer reveals")
    return kept, errors + _spread_errors(approved, kept)


def write_book(data_dir: str | os.PathLike[str], book: str) -> Path:
    approved = load_approved(data_dir, book)
    path = eval_dir(data_dir, book) / "book.txt"
    _write_text(path, book_text(approved))
    return path


def save_questions(data_dir: str | os.PathLike[str], book: str, reply: str) -> list[str]:
    """Verify the writer's reply and save it as questions.json; returns the errors (saved iff none).

    Raises ValueError when questions are already saved: answers are keyed to them.
    """
    approved = load_approved(data_dir, book)
    path = _questions_path(data_dir, book)
    if path.exists():
        raise ValueError(f"{path}: questions already saved; delete {path.parent} to start the evaluation over")
    data, errors = parse_reply(reply)
    if data is None:
        return errors
    kept, errors = verify_questions(approved, data)
    if errors:
        return errors
    saved = {"book": data["book"], "questions": kept}
    if "alias_note" in data:
        saved["alias_note"] = data["alias_note"]
    _write_text(path, json.dumps(saved, ensure_ascii=False, indent=1) + "\n")
    return []


def _load_questions(data_dir: str | os.PathLike[str], book: str) -> list[dict]:
    path = _questions_path(data_dir, book)
    if not path.exists():
        raise FileNotFoundError(f"{path}: no questions yet; run questions first")
    return json.loads(path.read_text(encoding="utf-8"))["questions"]


def _passes_check(approved: dict, question: dict, answer: str) -> bool:
    return not check_answer(answer, approved, parse_position(approved, question["position"]))


def _judge_message(approved: dict, question: dict, answer: str) -> str:
    at = parse_position(approved, question["position"])
    lines = [
        f"question: {_one_line(question['question'])}",
        f"position: {_position_text(approved, at)}",
        f"expected: {_one_line(question['expected'])}",
        f"quote: {_one_line(question['quote'])}",
    ]
    if question["kind"] in TRAP_KINDS:
        lines.append("trap: yes")
    if question["kind"] == "alias":
        lines += [f"alias: {_one_line(question['alias'])}", f"hidden: {_one_line(question['hidden'])}"]
    lines += ["answer:", answer.strip()]
    return "\n".join(lines)


def next_step(data_dir: str | os.PathLike[str], book: str) -> dict:
    """The next step of the loop: ask the first unanswered question, judge the first answer
    that passed the cut check and has no judgement, or done.

    An answer that failed the cut check is never judged: the reader never saw it.
    """
    approved = load_approved(data_dir, book)
    for q in _load_questions(data_dir, book):
        answer_path = _answer_path(data_dir, book, q["id"])
        if not answer_path.exists():
            at = parse_position(approved, q["position"])
            return {"step": "ask", "id": q["id"], "position": _position_text(approved, at),
                    "question": _one_line(q["question"])}
        answer = answer_path.read_text(encoding="utf-8")
        if _passes_check(approved, q, answer) and not _judgement_path(data_dir, book, q["id"]).exists():
            return {"step": "judge", "id": q["id"], "message": _judge_message(approved, q, answer)}
    return {"step": "done"}


def _require_current(data_dir: str | os.PathLike[str], book: str, step: str, qid: str) -> None:
    current = next_step(data_dir, book)
    if current["step"] != step or current["id"] != qid:
        now = f"{current['step']} {current['id']}" if "id" in current else current["step"]
        raise ValueError(f"{step} {qid} is not the current step (current: {now})")


def save_answer(data_dir: str | os.PathLike[str], book: str, qid: str, answer: str) -> None:
    """Save what /ask-book replied for the current ask step, verbatim. Raises ValueError otherwise."""
    _require_current(data_dir, book, "ask", qid)
    _write_text(_answer_path(data_dir, book, qid), answer)


def parse_judgement(text: str) -> tuple[dict | None, list[str]]:
    """The judge's reply as {verdict, leak, reason}, or (None, errors)."""
    body = text.strip()
    if "\n" in body:
        return None, ["reply with one JSON line and nothing else"]
    try:
        obj = json.loads(body)
    except json.JSONDecodeError as e:
        return None, [f"the reply is not valid JSON ({e.msg}); reply with one JSON line and nothing else"]
    if not isinstance(obj, dict):
        return None, ["the reply must be one JSON object"]
    errors = [f'missing field "{f}"' for f in JUDGEMENT_FIELDS if f not in obj]
    errors += [f'unknown field "{k}"' for k in obj if k not in JUDGEMENT_FIELDS]
    if "verdict" in obj and obj["verdict"] not in VERDICTS:
        errors.append('"verdict" must be "right" or "wrong"')
    if "leak" in obj and not isinstance(obj["leak"], bool):
        errors.append('"leak" must be true or false')
    if "reason" in obj and (not isinstance(obj["reason"], str) or not obj["reason"].strip()):
        errors.append('"reason" must be a non-empty string')
    return (None, errors) if errors else (obj, [])


def save_judgement(data_dir: str | os.PathLike[str], book: str, qid: str, reply: str) -> list[str]:
    """Validate and save the judge's reply for the current judge step; returns the errors (saved iff none).

    Raises ValueError when `qid` is not the current judge step.
    """
    _require_current(data_dir, book, "judge", qid)
    judgement, errors = parse_judgement(reply)
    if judgement is None:
        return errors
    _write_text(_judgement_path(data_dir, book, qid), json.dumps(judgement, ensure_ascii=False) + "\n")
    return []


def _cites_a_listed_section(approved: dict, question: dict, answer: str) -> bool:
    chapter0 = _chapter0_labels(approved)
    listed = set(question["sections"])
    for label, scene in citations(answer):
        if scene is None and label in chapter0 and listed & chapter0:
            return True
        if scene is not None and label in listed:
            return True
    return False


def _result(approved: dict, question: dict, answer: str, judgement: dict | None) -> dict:
    trap = question["kind"] in TRAP_KINDS
    out = {"id": question["id"], "kind": question["kind"]}
    if judgement is None:
        return out | ({"leak": False, "why": "failed the cut check; the reader saw no answer"} if trap
                      else {"right": False, "why": "failed the cut check"})
    if trap:
        named = (question["kind"] == "alias"
                 and normalise(question["hidden"]).casefold() in normalise(answer).casefold())
        why = ("judge flagged a leak" if judgement["leak"]
               else "names the hidden identity" if named else "no leak")
        return out | {"leak": judgement["leak"] or named, "why": why}
    if judgement["verdict"] != "right":
        return out | {"right": False, "why": f"judge: {judgement['reason']}"}
    if not _cites_a_listed_section(approved, question, answer):
        return out | {"right": False, "why": "no citation names a section that holds the fact"}
    return out | {"right": True, "why": "right"}


def score(data_dir: str | os.PathLike[str], book: str) -> dict:
    """Score a finished run and save it as score.json. Raises ValueError while steps remain."""
    approved = load_approved(data_dir, book)
    current = next_step(data_dir, book)
    if current["step"] != "done":
        raise ValueError(f"not finished: {current['step']} {current['id']} is next; run next")
    results = []
    for q in _load_questions(data_dir, book):
        answer = _answer_path(data_dir, book, q["id"]).read_text(encoding="utf-8")
        judged = _judgement_path(data_dir, book, q["id"])
        judgement = json.loads(judged.read_text(encoding="utf-8")) if judged.exists() else None
        results.append(_result(approved, q, answer, judgement))
    accuracy = [r for r in results if "right" in r]
    traps = [r for r in results if "leak" in r]
    right = sum(r["right"] for r in accuracy)
    leaks = sum(r["leak"] for r in traps)
    passed = right >= PASS_ACCURACY and leaks <= PASS_LEAKS
    line = (f"{book}: accuracy {right}/{len(accuracy)} · leaks {leaks} of {len(traps)} traps · "
            f"{'PASS' if passed else 'FAIL'} (bar {PASS_ACCURACY}/{ACCURACY_TOTAL}, {PASS_LEAKS} leaks)")
    result = {"book": book, "accuracy": right, "accuracy_total": len(accuracy), "leaks": leaks,
              "traps": len(traps), "passed": passed, "line": line, "results": results}
    aliases = sum(q["kind"] == "alias" for q in _load_questions(data_dir, book))
    if aliases < ALIAS_MIN:
        note = json.loads(_questions_path(data_dir, book).read_text(encoding="utf-8")).get("alias_note", "")
        result["alias_note"] = note
        result["line"] = f"{line} · {aliases} alias traps, under {ALIAS_MIN}: {_one_line(note)}"
    _write_text(eval_dir(data_dir, book) / "score.json", json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    return result


def recap_positions(approved: dict) -> list[str]:
    """The middle story scene of each third of the book's scenes, in reading order."""
    scenes = [Position(s.order, k) for s in slots(approved) if not s.chapter0 for k in range(1, len(s.scenes) + 1)]
    thirds: list[list[Position]] = [[] for _ in _THIRDS]
    for i, at in enumerate(scenes):
        thirds[i * len(_THIRDS) // len(scenes)].append(at)
    return [_position_text(approved, third[len(third) // 2]) for third in thirds if third]


def _recaps(approved: dict) -> list[tuple[str, str]]:
    return [(f"r{i}", position) for i, position in enumerate(recap_positions(approved), 1)]


def _recap_judge_message(data_dir: str | os.PathLike[str], book: str, approved: dict, at: Position,
                         recap: str) -> str:
    """The position, the material the bot was given, every indexed scene summary after the
    position, and the recap last."""
    chapter0, sections = read_outputs(data_dir, book, approved, at)
    material = render_recap(build_recap(chapter0, sections, at), position_line(_labels(approved)[at.order], at.scene))
    _, everything = read_outputs(data_dir, book, approved, Position(max(_labels(approved)), 0))
    later = [f"- {label} · {s['scene']}: {_one_line(s['summary'])}"
             for order, label, record in everything for s in record["scenes"] if (order, s["scene"]) > at]
    return "\n".join([f"position: {_position_text(approved, at)}", "material:", material.strip(),
                      "later:", *(later or ["(nothing)"]), "recap:", recap.strip()])


def recap_next_step(data_dir: str | os.PathLike[str], book: str) -> dict:
    """{"step": "ask", "id", "position"}, {"step": "judge", "id", "message"} or {"step": "done"}.

    A recap that fails the cut check is never judged, as in next_step; recap_score reports it.
    """
    approved = load_approved(data_dir, book)
    for rid, position in _recaps(approved):
        path = _recap_path(data_dir, book, rid)
        if not path.exists():
            return {"step": "ask", "id": rid, "position": position}
        recap = path.read_text(encoding="utf-8")
        at = parse_position(approved, position)
        if not check_answer(recap, approved, at) and not _recap_judgement_path(data_dir, book, rid).exists():
            return {"step": "judge", "id": rid, "message": _recap_judge_message(data_dir, book, approved, at, recap)}
    return {"step": "done"}


def _require_recap_current(data_dir: str | os.PathLike[str], book: str, step: str, rid: str) -> None:
    current = recap_next_step(data_dir, book)
    if current["step"] != step or current["id"] != rid:
        now = f"{current['step']} {current['id']}" if "id" in current else current["step"]
        raise ValueError(f"{step} {rid} is not the current recap step (current: {now})")


def save_recap(data_dir: str | os.PathLike[str], book: str, rid: str, recap: str) -> None:
    """Save the bot's recap for the current recap ask step, verbatim. Raises ValueError otherwise."""
    _require_recap_current(data_dir, book, "ask", rid)
    _write_text(_recap_path(data_dir, book, rid), recap)


def parse_recap_judgement(text: str) -> tuple[dict | None, list[str]]:
    """The recap judge's reply as {leak, reason}, or (None, errors)."""
    body = text.strip()
    if "\n" in body:
        return None, ["reply with one JSON line and nothing else"]
    try:
        obj = json.loads(body)
    except json.JSONDecodeError as e:
        return None, [f"the reply is not valid JSON ({e.msg}); reply with one JSON line and nothing else"]
    if not isinstance(obj, dict):
        return None, ["the reply must be one JSON object"]
    errors = [f'missing field "{f}"' for f in RECAP_JUDGEMENT_FIELDS if f not in obj]
    errors += [f'unknown field "{k}"' for k in obj if k not in RECAP_JUDGEMENT_FIELDS]
    if "leak" in obj and not isinstance(obj["leak"], bool):
        errors.append('"leak" must be true or false')
    if "reason" in obj and (not isinstance(obj["reason"], str) or not obj["reason"].strip()):
        errors.append('"reason" must be a non-empty string')
    return (None, errors) if errors else (obj, [])


def save_recap_judgement(data_dir: str | os.PathLike[str], book: str, rid: str, reply: str) -> list[str]:
    """Validate and save the recap judge's reply for the current judge step; returns the errors
    (saved iff none). Raises ValueError when `rid` is not the current recap judge step."""
    _require_recap_current(data_dir, book, "judge", rid)
    judgement, errors = parse_recap_judgement(reply)
    if judgement is None:
        return errors
    _write_text(_recap_judgement_path(data_dir, book, rid), json.dumps(judgement, ensure_ascii=False) + "\n")
    return []


def recap_score(data_dir: str | os.PathLike[str], book: str) -> dict:
    """Score the finished recaps and save recap-score.json. Raises ValueError while steps remain
    or a recap has no fence record. Passes when every recap passed the cut check and was judged, with no leak, no later
    citation and no fence breach;
    the word count is reported only."""
    approved = load_approved(data_dir, book)
    current = recap_next_step(data_dir, book)
    if current["step"] != "done":
        raise ValueError(f"not finished: {current['step']} {current['id']} is next; run recap-next")
    order_of = {label: order for order, label in _labels(approved).items()}
    results = []
    for rid, position in _recaps(approved):
        fence = recap_fence_path(data_dir, book, rid)
        if not fence.exists():
            raise ValueError(f"no fence record for {rid}: {fence}")
        at = parse_position(approved, position)
        recap = _recap_path(data_dir, book, rid).read_text(encoding="utf-8")
        later = sum(1 for label, k in citations(recap)
                    if k is not None and label in order_of and (order_of[label], k) > at)
        judged = _recap_judgement_path(data_dir, book, rid)
        judgement = json.loads(judged.read_text(encoding="utf-8")) if judged.exists() else None
        results.append({
            "id": rid, "position": position, "checked": judgement is not None,
            "leak": bool(judgement and judgement["leak"]),
            "why": judgement["reason"] if judgement else "failed check",
            "later_citations": later,
            "breach": bool(json.loads(fence.read_text(encoding="utf-8"))["breach"]),
            "words": len(recap.strip().partition("\n")[2].split()),
        })
    checked = sum(r["checked"] for r in results)
    leaks = sum(r["leak"] for r in results)
    later = sum(r["later_citations"] for r in results)
    breaches = sum(r["breach"] for r in results)
    passed = checked == len(results) and leaks == 0 and later == 0 and breaches == 0
    line = (f"recaps: {checked}/{len(results)} · leaks {leaks} · later citations {later} · "
            f"fence breaches {breaches} · words {', '.join(str(r['words']) for r in results)}")
    result = {"book": book, "checked": checked, "recaps": len(results), "leaks": leaks, "later_citations": later,
              "breaches": breaches, "passed": passed, "line": line, "results": results}
    _write_text(eval_dir(data_dir, book) / "recap-score.json", json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    return result


def _question_id(value: str) -> str:
    if not _ID.match(value):
        raise argparse.ArgumentTypeError(f"id may hold only letters, digits, '-' and '_': {value!r}")
    return value


def _print_errors(errors: list[str]) -> int:
    for e in errors:
        print(e, file=sys.stderr)
    return 1 if errors else 0


def _run(args: argparse.Namespace, data_dir: Path) -> int:
    if args.command == "book":
        print(json.dumps({"book": str(write_book(data_dir, args.book).resolve())}, ensure_ascii=False))
        return 0
    if args.command == "questions":
        errors = save_questions(data_dir, args.book, sys.stdin.read())
        if errors:
            return _print_errors(errors)
        count = len(_load_questions(data_dir, args.book))
        print(json.dumps({"questions": str(_questions_path(data_dir, args.book).resolve()), "count": count},
                         ensure_ascii=False))
        return 0
    if args.command == "next":
        print(json.dumps(next_step(data_dir, args.book), ensure_ascii=False))
        return 0
    if args.command == "answer":
        save_answer(data_dir, args.book, args.id, sys.stdin.read())
        print(json.dumps({"saved": args.id}))
        return 0
    if args.command == "judge":
        errors = save_judgement(data_dir, args.book, args.id, sys.stdin.read())
        if errors:
            return _print_errors(errors)
        print(json.dumps({"saved": args.id}))
        return 0
    if args.command == "recap-next":
        print(json.dumps(recap_next_step(data_dir, args.book), ensure_ascii=False))
        return 0
    if args.command == "recap-judge":
        errors = save_recap_judgement(data_dir, args.book, args.id, sys.stdin.read())
        if errors:
            return _print_errors(errors)
        print(json.dumps({"saved": args.id}))
        return 0
    result = recap_score(data_dir, args.book) if args.command == "recap-score" else score(data_dir, args.book)
    print(result["line"])
    return 0 if result["passed"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m core.evaluate", description="Evaluate a book's index.")
    sub = parser.add_subparsers(dest="command", required=True)
    commands = {
        "book": "write eval/book.txt, the labelled book the question writer reads",
        "questions": "check the question file on stdin and save it; exit 0 saved, 1 rejected",
        "next": "print the next ask or judge step",
        "answer": "save what /ask-book replied (stdin) for the current ask step",
        "judge": "check and save the judge's reply (stdin); exit 0 saved, 1 rejected",
        "score": "print the score; exit 0 pass, 1 fail",
        "recap-next": "print the next recap ask or judge step",
        "recap-judge": "check and save the recap judge's reply (stdin); exit 0 saved, 1 rejected",
        "recap-score": "print the recap score; exit 0 pass, 1 fail",
    }
    for name, help_text in commands.items():
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("book")
        if name in ("answer", "judge", "recap-judge"):
            cmd.add_argument("id", type=_question_id)
        cmd.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help="parent of the per-book folders")
    args = parser.parse_args(argv)
    try:
        return _run(args, Path(args.data_dir))
    except (FileNotFoundError, SplitNotApproved, ValueError) as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
