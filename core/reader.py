"""The reader's saved position for one book, and the position questions the bot asks of it.

The position lives in <data-dir>/<book>/reader.json ({"label", "scene", "order"}), apart from
cut/position.json, which every cut rebuild replaces. Only `move_to` writes it, and only after
the cut was rebuilt there; `fresh_cut` rebuilds a cut that drifted from it. A section past
the reader is never opened: `find_scenes` reads outputs only up to its limit.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal, NamedTuple

from core.brief import Position
from core.cut import POSITION_FILE, _content_words, _story, build_cut, cut_dir, parse_position
from core.extract import Slot, _write_text, read_outputs
from core.split import load_approved

READER_FILE = "reader.json"
SENTENCE_WORDS = 40
LIMIT_HINT = 'give a section as the book prints it, or a rough percentage like "about 40%"'

_PERCENT = re.compile(r"(?:about|around|roughly|~)?\s*(\d{1,3}(?:\.\d+)?)\s*%", re.IGNORECASE)
_SENTENCE_END = re.compile(r"[.!?][\"'”’»)\]]*(?=\s|$)")


class Match(NamedTuple):
    label: str
    scene: int
    sentence: str  # the scene's first sentence, from the book's own text


def _reader_path(data_dir: str | os.PathLike[str], book: str) -> Path:
    return Path(data_dir) / book / READER_FILE


def saved_position(data_dir: str | os.PathLike[str], book: str) -> Position | None:
    """The reader's saved position, or None before the first `move_to`."""
    path = _reader_path(data_dir, book)
    if not path.exists():
        return None
    saved = json.loads(path.read_text(encoding="utf-8"))
    return Position(saved["order"], saved["scene"])


def move_to(data_dir: str | os.PathLike[str], book: str, upto: Position) -> Path:
    """Rebuild cut/ at `upto`, then save `upto` as the reader's position; returns the cut.

    Raises what build_cut raises (a position the book does not have, one not indexed yet),
    and then nothing is saved.
    """
    cut = build_cut(data_dir, book, upto)
    label = json.loads((cut / POSITION_FILE).read_text(encoding="utf-8"))["label"]
    reader = {"label": label, "scene": upto.scene, "order": upto.order}
    _write_text(_reader_path(data_dir, book), json.dumps(reader, ensure_ascii=False, indent=2) + "\n")
    return cut


def fresh_cut(data_dir: str | os.PathLike[str], book: str) -> Position | None:
    """The saved position, with cut/ rebuilt at it when missing or at another position.

    A crash between the two writes of `move_to` leaves the cut ahead of the saved position;
    this is what puts it back. None, and no cut touched, before the first `move_to`.
    """
    saved = saved_position(data_dir, book)
    if saved is None:
        return None
    path = cut_dir(data_dir, book) / POSITION_FILE
    cut = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if cut is None or Position(cut["order"], cut["scene"]) != saved:
        build_cut(data_dir, book, saved)
    return saved


def _story_slot(approved: dict, label: str) -> Slot:
    """The story section labelled exactly `label`; otherwise parse_position's own refusal."""
    label = label.strip()
    for slot in _story(approved):
        if slot.section == label:
            return slot
    parse_position(approved, f"{label} · 1")
    raise ValueError(f"unknown section {label!r}")


def bare_section(approved: dict, text: str) -> str | None:
    """The story label when `text` is exactly one, with no scene; else None."""
    text = text.strip()
    return text if any(s.section == text for s in _story(approved)) else None


def section_edge(approved: dict, label: str, edge: Literal["start", "end"]) -> Position:
    """Scene 1 ("start") or the last scene ("end") of a story section.

    Raises ValueError with parse_position's text for an unknown or chapter-0 label.
    """
    slot = _story_slot(approved, label)
    if edge == "start":
        return Position(slot.order, 1)
    if edge == "end":
        return Position(slot.order, len(slot.scenes))
    raise ValueError(f"edge must be 'start' or 'end', not {edge!r}")


def relation(approved: dict, saved: Position, label: str) -> Literal["behind", "here", "past"]:
    """Where a named section lies against the reader's saved position, by running order.

    A chapter-0 label is behind: it is in every cut. Raises ValueError with parse_position's
    text for an unknown label.
    """
    if label.strip() in {u["label"] for u in approved["chapter0"]}:
        return "behind"
    order = _story_slot(approved, label).order
    if order < saved.order:
        return "behind"
    return "here" if order == saved.order else "past"


def limit_order(approved: dict, marker: str) -> int:
    """The running order a search may reach: a story label's own, or for a percentage
    ("40%", "about 40%") the section holding that point of the story by word count.

    Raises ValueError (LIMIT_HINT) for anything else.
    """
    marker = marker.strip()
    story = _story(approved)
    for slot in story:
        if slot.section == marker:
            return slot.order
    m = _PERCENT.fullmatch(marker)
    if m is None or float(m[1]) > 100:
        raise ValueError(LIMIT_HINT)
    words = {u["order"]: u["words"] for u in approved["sections"]}
    target = sum(words[s.order] for s in story) * float(m[1]) / 100
    reached = 0
    for slot in story:
        reached += words[slot.order]
        if reached >= target:
            return slot.order
    return story[-1].order


def first_sentence(paragraphs: tuple[str, ...] | list[str]) -> str:
    """The first paragraph up to its first sentence end, capped at SENTENCE_WORDS words."""
    if not paragraphs:
        return ""
    text = " ".join(paragraphs[0].split())
    end = _SENTENCE_END.search(text)
    words = (text[:end.end()] if end else text).split()
    if len(words) > SENTENCE_WORDS:
        return " ".join(words[:SENTENCE_WORDS]) + "…"
    return " ".join(words)


def find_scenes(data_dir: str | os.PathLike[str], book: str, description: str,
                lo: int, hi: int, n: int = 3) -> list[Match]:
    """At most `n` scenes of sections `lo`..`hi` (running order) that best fit a description.

    A scene scores one per distinct content word it shares with the description, over its
    indexed summary, events, character names and terms; zero scores are dropped and ties go
    to the earlier scene. Opens no output past `hi`, and returns no summary, only the
    label, the scene and its first sentence.
    """
    if not 1 <= lo <= hi:
        raise ValueError(f"bad search range {lo}..{hi}")
    approved = load_approved(data_dir, book)
    slots = {s.order: s for s in _story(approved) if lo <= s.order <= hi}
    if not slots:
        return []
    last = slots[max(slots)]
    wanted = _content_words(description)
    _, sections = read_outputs(data_dir, book, approved, Position(last.order, len(last.scenes)))
    scored: list[tuple[int, int, int]] = []
    for order, _, record in sections:
        if order not in slots:
            continue
        for scene in record["scenes"]:
            text = " ".join([scene["summary"], *scene["events"], *(c["name"] for c in scene["characters"]),
                             *(t["term"] for t in scene["terms"])])
            score = len(wanted & _content_words(text))
            if score:
                scored.append((-score, order, scene["scene"]))
    return [Match(slots[order].section, k, first_sentence(slots[order].scenes[k - 1]))
            for _, order, k in sorted(scored)[:n]]
