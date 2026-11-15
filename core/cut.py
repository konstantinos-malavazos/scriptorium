"""The cut: the merge up to the reader's position, written to <data-dir>/<book>/cut/, and the
code check on an answer drawn from it.

    python -m core.cut build <book> <position> [--data-dir DIR]
    python -m core.cut check <book> [--data-dir DIR]        (the answer on stdin)

`build` parses the position against the approved split, refuses one whose sections are not
all indexed yet, rebuilds cut/ from scratch (characters.md, glossary.md, timeline.md,
position.json) and prints one JSON line, {"cut", "line"} (absolute cut path, the answer's
opening line). `check` reads the position from cut/position.json and prints one error per
line to stderr.

Exit codes: build 0 written; check 0 passed, 1 failed; both 2 on a missing or unapproved
split, a bad position, an index still in progress, or (check) no cut yet.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from core.brief import CHAPTER0_LABEL, Position
from core.extract import Slot, read_outputs, slots
from core.merge import merge, render_characters, render_glossary, render_timeline
from core.split import DEFAULT_DATA_DIR, SplitNotApproved, load_approved

FALLBACK = "That's not in what you've read so far."
POSITION_FILE = "position.json"

_POSITION = (
    re.compile(r"(?P<label>.+?)\s*·\s*(?:scene\s+)?(?P<scene>\d+)"),
    re.compile(r"(?P<label>.+?)\s+scene\s+(?P<scene>\d+)"),
    re.compile(r"(?P<label>.+?)/(?P<scene>\d+)"),
)
_CITATION = re.compile(r"\[([^\[\]]*)\]")


def cut_dir(data_dir: str | os.PathLike[str], book: str) -> Path:
    return Path(data_dir) / book / "cut"


def position_line(label: str, scene: int) -> str:
    return f"Up to {label} · scene {scene}:"


def _story(approved: dict) -> list[Slot]:
    return [s for s in slots(approved) if not s.chapter0]


def _chapter0_labels(approved: dict) -> list[str]:
    return [u["label"] for u in approved["chapter0"]]


def _example(label: str) -> str:
    return f'give it as "{label} · scene <k>"'


def parse_position(approved: dict, text: str) -> Position:
    """Position from `<label> · scene <k>`, `<label> · <k>`, `<label> scene <k>` or `<label>/<k>`.

    The label must equal a story section's label exactly and k must be one of its scenes.
    Raises ValueError: "which scene?" for a bare section, a scene it does not have, or a
    chapter-0 unit; "unknown section" otherwise, which never lists the sections.
    """
    text = text.strip()
    story = {s.section: s for s in _story(approved)}
    named = [(m["label"].strip(), int(m["scene"])) for p in _POSITION if (m := p.fullmatch(text))]
    for label, k in named:
        if label in story:
            slot = story[label]
            if not 1 <= k <= len(slot.scenes):
                raise ValueError(f"which scene? {label} has no scene {k}; {_example(label)}")
            return Position(slot.order, k)
    if text in story:
        raise ValueError(f"which scene? {_example(text)}")
    label = named[0][0] if named else text
    first = next(iter(story))
    if label in {*_chapter0_labels(approved), CHAPTER0_LABEL}:
        raise ValueError(f"which scene? a position is a story section and a scene; {_example(first)}")
    raise ValueError(f"unknown section {label!r}; use the label as the book prints it, {_example(first)}")


def _slot_at(approved: dict, upto: Position) -> Slot:
    for slot in _story(approved):
        if slot.order == upto.order:
            if not 1 <= upto.scene <= len(slot.scenes):
                raise ValueError(f"which scene? {slot.section} has no scene {upto.scene}")
            return slot
    raise ValueError(f"unknown section at order {upto.order}")


def build_cut(data_dir: str | os.PathLike[str], book: str, upto: Position) -> Path:
    """Rebuild cut/ at `upto` from the saved outputs; returns the cut directory.

    Raises ValueError for a position the book does not have, a label holding a square
    bracket (it would break citation parsing), or a section at or before `upto` not yet
    indexed ("still indexing, at <label>").
    """
    approved = load_approved(data_dir, book)
    slot = _slot_at(approved, upto)
    labels = _chapter0_labels(approved)
    for label in [*labels, *(s.section for s in _story(approved))]:
        if "[" in label or "]" in label:
            raise ValueError(f"label {label!r} holds a square bracket, which citations reserve")
    chapter0, sections = read_outputs(data_dir, book, approved, upto)
    if labels and chapter0 is None:
        raise ValueError(f"still indexing, at {labels[0]}")
    done = {order for order, _, _ in sections}
    for s in _story(approved):
        if s.order <= upto.order and s.order not in done:
            raise ValueError(f"still indexing, at {s.section}")

    merged = merge(chapter0, sections, upto, labels)
    position = {"book": book, "label": slot.section, "scene": upto.scene, "order": upto.order,
                "line": position_line(slot.section, upto.scene), "chapter0_labels": labels}
    files = {
        "characters.md": render_characters(merged),
        "glossary.md": render_glossary(merged),
        "timeline.md": render_timeline(merged),
        POSITION_FILE: json.dumps(position, ensure_ascii=False, indent=2) + "\n",
    }
    cut = cut_dir(data_dir, book)
    cut.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=cut.parent, prefix=".cut-"))
    try:
        for name, text in files.items():
            (tmp / name).write_text(text, encoding="utf-8")
        if cut.exists():
            shutil.rmtree(cut)
        os.replace(tmp, cut)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            shutil.rmtree(tmp)
        raise
    return cut


def check_answer(answer: str, approved: dict, upto: Position) -> list[str]:
    """The errors in an answer at `upto`; empty means it passes.

    The first line must be the position line. Every `[...]` after it must be a chapter-0
    label or `<story label> · <scene>` at or behind the reader. A section past the reader is
    reported before its scene count is looked at, and a later scene of the reader's own section
    before the scene range, so no error reveals how much of the book is left.
    An answer with no citation must be exactly FALLBACK after the first line.
    """
    slot = _slot_at(approved, upto)
    labels = _chapter0_labels(approved)
    story = {s.section: s for s in _story(approved)}
    expected = position_line(slot.section, upto.scene)
    first, _, body = answer.strip().partition("\n")
    body = body.strip()
    errors = []
    if first.rstrip() != expected:
        errors.append(f"the first line must be exactly {expected!r}")
    hint = "cite [<section> · <scene>]" + (f" or a chapter-0 label: {', '.join(f'[{x}]' for x in labels)}"
                                           if labels else "")
    citations = _CITATION.findall(body)
    for cited in citations:
        if cited in labels:
            continue
        label, sep, k = cited.rpartition(" · ")
        if not sep or not (k.isascii() and k.isdigit()) or label not in story:
            errors.append(f"[{cited}]: unknown citation; {hint}")
        elif story[label].order > upto.order:
            errors.append(f"[{cited}]: a section past the reader's")
        elif story[label].order == upto.order and int(k) > upto.scene:
            errors.append(f"[{cited}]: a scene past the reader's")
        elif not 1 <= int(k) <= len(story[label].scenes):
            errors.append(f"[{cited}]: a scene the section doesn't have")
    if FALLBACK in body and body != FALLBACK:
        errors.append(f"the fallback must be the whole answer after the first line: {FALLBACK!r}")
    elif not citations and body != FALLBACK:
        errors.append(f"no citation: every fact needs one, or the answer must be exactly {FALLBACK!r}")
    return list(dict.fromkeys(errors))


def _build(book: str, position: str, data_dir: Path) -> int:
    approved = load_approved(data_dir, book)
    upto = parse_position(approved, position)
    cut = build_cut(data_dir, book, upto)
    line = position_line(_slot_at(approved, upto).section, upto.scene)
    print(json.dumps({"cut": str(cut.resolve()), "line": line}, ensure_ascii=False))
    return 0


def _check(book: str, data_dir: Path) -> int:
    path = cut_dir(data_dir, book) / POSITION_FILE
    if not path.exists():
        raise FileNotFoundError(f"{path}: no cut yet; run build first")
    saved = json.loads(path.read_text(encoding="utf-8"))
    errors = check_answer(sys.stdin.read(), load_approved(data_dir, book), Position(saved["order"], saved["scene"]))
    for e in errors:
        print(e, file=sys.stderr)
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m core.cut", description="Cut a book's index at a position.")
    sub = parser.add_subparsers(dest="command", required=True)
    bld = sub.add_parser("build", help="rebuild cut/ at the position and print where it is")
    bld.add_argument("book")
    bld.add_argument("position", help='e.g. "ch 6 · scene 3", "ch 6 · 3", "ch 6 scene 3" or "ch 6/3"')
    bld.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help="parent of the per-book folders")
    chk = sub.add_parser("check", help="check the answer on stdin; exit 0 passed, 1 failed")
    chk.add_argument("book")
    chk.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir)
    try:
        if args.command == "build":
            return _build(args.book, args.position, data_dir)
        return _check(args.book, data_dir)
    except (FileNotFoundError, SplitNotApproved, ValueError) as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
