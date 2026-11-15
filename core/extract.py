"""Index a book: one sequential worker call per section, chapter 0 first, over an approved split.

    python -m core.extract next <book> [--instructions PATH] [--data-dir DIR]
    python -m core.extract submit <book> <slot> [--data-dir DIR]

`next` writes the current slot's prompt to work/<slot>.prompt.md and prints one JSON line,
{"slot","section","attempt","prompt","reply"} (absolute paths), or {"done": true}.
`submit` reads work/<slot>.reply.json, validates it, saves or rejects it, deletes the reply,
and prints {"outcome","errors","warnings"} (warnings is a count).

Exit codes: next 0 ok; submit 0 accepted, 3 rejected (the next `next` is the retry),
1 rejected twice (stopped); both 2 on a missing or unapproved split, a missing
instructions or reply file, or a slot that is not the current one.

All state is on disk under <data-dir>/<book>/: sections/<slot>.json marks a slot done, and
the attempt number is one more than the count of sections/<slot>.rejected-<n>.txt files.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from core.brief import CHAPTER0_LABEL, Position, build_brief, render_brief
from core.split import DEFAULT_DATA_DIR, SplitNotApproved, load_approved
from core.validate import validate

INSTRUCTIONS = Path(__file__).resolve().parent.parent / "prompts" / "worker-instructions.md"
_SLOT_ID = re.compile(r"s\d{2,}\Z")


@dataclass(frozen=True)
class Slot:
    """One worker call: chapter 0 (s00) or one story section (s<order:02d>)."""

    id: str
    section: str  # the exact "section" value the reply must carry
    order: int  # 0 = chapter 0
    chapter0: bool
    heading: tuple[str, ...]
    scenes: tuple[tuple[str, ...], ...]  # paragraphs per scene
    parts: tuple[tuple[str, tuple[str, ...]], ...] = ()  # chapter 0 only: (label, paragraphs) per unit

    @property
    def scene_texts(self) -> list[str]:
        """The text each scene's quotes are checked against; the heading counts in scene 1."""
        texts = ["\n".join(paras) for paras in self.scenes]
        if self.parts:
            texts[0] = "\n".join(line for label, paras in self.parts for line in (label, *paras))
        if self.heading:
            texts[0] = "\n".join((*self.heading, texts[0]))
        return texts


@dataclass(frozen=True)
class Step:
    slot: Slot
    attempt: int  # odd = a first try, even = the one retry
    prompt: str
    brief: dict


class Outcome(str, Enum):
    ACCEPTED = "accepted"
    RETRY = "retry"
    STOPPED = "stopped"


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    errors: list[str]
    warnings: list[str]


def slots(approved: dict) -> list[Slot]:
    """The slots in reading order: s00 when there is a chapter 0, then one per story section."""
    out = []
    if approved["chapter0"]:
        parts = tuple((u["label"], tuple(u["paragraphs"])) for u in approved["chapter0"])
        out.append(Slot("s00", CHAPTER0_LABEL, 0, True, (),
                        (tuple(p for _, paras in parts for p in paras),), parts))
    for u in approved["sections"]:
        out.append(Slot(f"s{u['order']:02d}", u["label"], u["order"], False,
                        tuple(h["text"] for h in u["heading"]),
                        tuple(tuple(sc["paragraphs"]) for sc in u["scenes"])))
    return out


def _sections_dir(data_dir: str | os.PathLike[str], book: str) -> Path:
    return Path(data_dir) / book / "sections"


def _work_dir(data_dir: str | os.PathLike[str], book: str) -> Path:
    return Path(data_dir) / book / "work"


def _write_text(path: Path, text: str) -> None:
    """Write atomically, so a failed write never leaves a half file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def _current(data_dir: str | os.PathLike[str], book: str, approved: dict) -> Slot | None:
    done = _sections_dir(data_dir, book)
    return next((s for s in slots(approved) if not (done / f"{s.id}.json").exists()), None)


def _attempt(data_dir: str | os.PathLike[str], book: str, slot_id: str) -> int:
    done = _sections_dir(data_dir, book)
    pattern = re.compile(rf"{slot_id}\.rejected-\d+\.txt\Z")
    rejected = [p for p in done.glob(f"{slot_id}.rejected-*.txt") if pattern.match(p.name)] if done.is_dir() else []
    return len(rejected) + 1


def read_outputs(data_dir: str | os.PathLike[str], book: str, approved: dict,
                 upto: Position) -> tuple[dict | None, list[tuple[int, str, dict]]]:
    """(chapter-0 record, [(order, label, record)]) for the saved outputs at or before `upto`.

    Opens only chapter 0 and orders <= upto.order; a slot with no output yet is skipped.
    """
    done = _sections_dir(data_dir, book)
    chapter0 = None
    sections = []
    for slot in slots(approved):
        if slot.order > upto.order or (slot.chapter0 and upto.order < 1):
            continue
        path = done / f"{slot.id}.json"
        if not path.exists():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        if slot.chapter0:
            chapter0 = record
        else:
            sections.append((slot.order, slot.section, record))
    return chapter0, sections


def _brief_for(data_dir: str | os.PathLike[str], book: str, approved: dict, slot: Slot) -> dict:
    upto = Position(slot.order, 0)
    return build_brief(*read_outputs(data_dir, book, approved, upto), upto)


def build_prompt(instructions: str, slot: Slot, brief: dict, errors: list[str]) -> str:
    """Instructions, the section's identity, the brief, the section text, and any rejection."""
    text: list[str] = []
    if slot.heading:
        text.append("\n\n".join(slot.heading))
    for k, paras in enumerate(slot.scenes, 1):
        if slot.parts:
            body = "\n\n".join(f"#### {label}\n\n" + "\n\n".join(p) for label, p in slot.parts)
        else:
            body = "\n\n".join(paras)
        text.append(f"### Scene {k}\n\n{body}")
    blocks = [
        instructions.rstrip(),
        f"## Your section\n\nsection: {json.dumps(slot.section, ensure_ascii=False)}\nscenes: {len(slot.scenes)}",
        f"## Brief\n\n{render_brief(brief)}",
        "## Section text\n\n" + "\n\n".join(text),
    ]
    if errors:
        blocks.append("## Your previous reply was rejected\n\n" + "\n".join(f"- {e}" for e in errors))
    return "\n\n".join(blocks) + "\n"


def next_step(data_dir: str | os.PathLike[str], book: str, instructions: str) -> Step | None:
    """The current slot's step, or None when every slot has output. Raises what load_approved raises."""
    approved = load_approved(data_dir, book)
    slot = _current(data_dir, book, approved)
    if slot is None:
        return None
    attempt = _attempt(data_dir, book, slot.id)
    errors: list[str] = []
    if attempt % 2 == 0:
        previous = _sections_dir(data_dir, book) / f"{slot.id}.rejected-{attempt - 1}.errors.txt"
        if previous.exists():
            errors = previous.read_text(encoding="utf-8").splitlines()
    brief = _brief_for(data_dir, book, approved, slot)
    return Step(slot, attempt, build_prompt(instructions, slot, brief, errors), brief)


def submit(data_dir: str | os.PathLike[str], book: str, slot_id: str, reply: str) -> Result:
    """Validate `reply` for the current slot; save it, or save the rejection.

    Raises ValueError when slot_id is not the current slot, plus what load_approved raises.
    """
    approved = load_approved(data_dir, book)
    slot = _current(data_dir, book, approved)
    if slot is None or slot.id != slot_id:
        raise ValueError(f"{slot_id} is not the current slot (current: {slot.id if slot else 'none, all done'})")
    brief = _brief_for(data_dir, book, approved, slot)
    record, errors, warnings = validate(reply, section=slot.section, scene_texts=slot.scene_texts,
                                        brief=brief, chapter0=slot.chapter0)
    done = _sections_dir(data_dir, book)
    if record is not None:
        if warnings:
            _write_text(done / f"{slot.id}.warnings.log", "\n".join(warnings) + "\n")
        _write_text(done / f"{slot.id}.json", json.dumps(record, ensure_ascii=False, indent=1) + "\n")
        return Result(Outcome.ACCEPTED, [], warnings)
    attempt = _attempt(data_dir, book, slot.id)
    # The .txt is written last: its presence is what counts the attempt.
    _write_text(done / f"{slot.id}.rejected-{attempt}.errors.txt", "\n".join(errors) + "\n")
    _write_text(done / f"{slot.id}.rejected-{attempt}.txt", reply)
    return Result(Outcome.STOPPED if attempt % 2 == 0 else Outcome.RETRY, errors, [])


def run(data_dir: str | os.PathLike[str], book: str, instructions: str,
        call_model: Callable[[Step], str]) -> int:
    """Drive every slot to output: 0 when all are done, 1 when a slot is stopped."""
    while True:
        step = next_step(data_dir, book, instructions)
        if step is None:
            return 0
        result = submit(data_dir, book, step.slot.id, call_model(step))
        if result.outcome is Outcome.STOPPED:
            return 1


def _slot_id(value: str) -> str:
    if not _SLOT_ID.match(value):
        raise argparse.ArgumentTypeError(f"slot must look like s00, s01, ...: {value!r}")
    return value


def _next(book: str, instructions_path: Path, data_dir: Path) -> int:
    try:
        instructions = instructions_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"no instructions file at {instructions_path}", file=sys.stderr)
        return 2
    step = next_step(data_dir, book, instructions)
    if step is None:
        print(json.dumps({"done": True}))
        return 0
    work = _work_dir(data_dir, book)
    reply = work / f"{step.slot.id}.reply.json"
    prompt = work / f"{step.slot.id}.prompt.md"
    reply.unlink(missing_ok=True)
    _write_text(prompt, step.prompt)
    print(json.dumps({"slot": step.slot.id, "section": step.slot.section, "attempt": step.attempt,
                      "prompt": str(prompt.resolve()), "reply": str(reply.resolve())}))
    return 0


def _submit(book: str, slot_id: str, data_dir: Path) -> int:
    load_approved(data_dir, book)  # validates the book name before it becomes a path
    reply_path = _work_dir(data_dir, book) / f"{slot_id}.reply.json"
    try:
        reply = reply_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"no reply at {reply_path}", file=sys.stderr)
        return 2
    result = submit(data_dir, book, slot_id, reply)
    reply_path.unlink(missing_ok=True)
    print(json.dumps({"outcome": result.outcome.value, "errors": result.errors, "warnings": len(result.warnings)},
                     ensure_ascii=False))
    return {Outcome.ACCEPTED: 0, Outcome.RETRY: 3, Outcome.STOPPED: 1}[result.outcome]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m core.extract", description="Index a book section by section.")
    sub = parser.add_subparsers(dest="command", required=True)
    nxt = sub.add_parser("next", help="write the current slot's prompt and print where it is")
    nxt.add_argument("book")
    nxt.add_argument("--instructions", type=Path, default=INSTRUCTIONS, help="the worker instructions file")
    nxt.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help="parent of the per-book folders")
    sbm = sub.add_parser("submit", help="validate the slot's reply; exit 0 accepted, 3 retry, 1 stopped")
    sbm.add_argument("book")
    sbm.add_argument("slot", type=_slot_id)
    sbm.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir)
    try:
        if args.command == "next":
            return _next(args.book, args.instructions, data_dir)
        return _submit(args.book, args.slot, data_dir)
    except (FileNotFoundError, SplitNotApproved, ValueError) as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
