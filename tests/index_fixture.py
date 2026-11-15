"""A synthetic approved split and a scriptable stub worker for the extract tests. Invented text only."""

from __future__ import annotations

import json
from pathlib import Path

from core.extract import Step
from core.split import _unit_words

# Each slot's section value -> (the name it introduces, a marker word found nowhere else).
CAST = {
    "chapter 0": ("Orrin Vale", "lanternwick"),
    "ch 1": ("Tamsin Rook", "quillfeather"),
    "ch 2": ("Corvin Ashby", "brambleglass"),
    "ch 3": ("Ilse Marrow", "thornwick"),
}


def _story(order: int, heading: list[dict], scenes: list[list[str]]) -> dict:
    unit = {
        "kind": "story", "order": order, "label": f"ch {order}", "heading": heading,
        "scenes": [{"scene": k, "opening": paras[0], "paragraphs": paras} for k, paras in enumerate(scenes, 1)],
    }
    unit["words"] = _unit_words(unit)
    return unit


def fixture_split() -> dict:
    cast = {"kind": "non-story", "id": "x1", "label": "Cast", "where": "before", "why": "before",
            "default": "always", "decision": "always",
            "paragraphs": ["Orrin Vale, keeper of the lanternwick ledger."]}
    cast["words"] = _unit_words(cast)
    units = [
        cast,
        _story(1, [{"role": "title", "text": "Chapter 1"}], [
            ["Tamsin Rook crossed the quillfeather bridge at dusk.", "Orrin Vale watched her from the tower."],
            ["Tamsin Rook slept under the quillfeather awning."],
        ]),
        _story(2, [{"role": "title", "text": "Chapter 2"},
                   {"role": "epigraph", "text": "The brambleglass hums for “the pale cartographer” alone. -Old Song"}], [
            ["Corvin Ashby polished brambleglass lenses all night."],
        ]),
        _story(3, [{"role": "title", "text": "Chapter 3"}], [
            ["Ilse Marrow, whom the dockers called “the salt\nwidow”, sold thornwick rope."],
            ["Ilse Marrow counted thornwick knots."],
            ["Ilse Marrow left the thornwick docks for good."],
        ]),
    ]
    return {"book": "fx", "source": "fx.epub", "source_words": sum(_unit_words(u) for u in units),
            "approved": True, "reasons": [], "flips": [], "units": units}


def write_split(data_dir: Path, book: str, split: dict) -> Path:
    path = data_dir / book / "split.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(split, ensure_ascii=False), encoding="utf-8")
    return path


def valid_reply(step: Step) -> dict:
    """A reply the validator accepts, with no warnings, built from the step's slot and brief."""
    section = step.slot.section
    name, marker = CAST[section]
    known = [c["name"] for c in step.brief["characters"]]
    scenes = []
    for k in range(1, len(step.slot.scenes) + 1):
        chars = [{"name": name, "line": f"does {marker} work, scene {k}", "new": name not in known}]
        if k == 1:
            chars += [{"name": n, "line": f"seen again in {section}", "new": False} for n in known]
        scenes.append({
            "scene": k, "pov": name,
            "summary": f"{name} does the {marker} work in {section}, scene {k}, before nightfall.",
            "events": [f"{name} meets the {marker}"], "characters": chars, "places": [],
            "terms": [{"term": marker, "meaning": f"a thing from {section}"}] if k == 1 else [],
            "open_questions": [],
        })
    return {"section": section, "scenes": scenes}


class StubWorker:
    """A call_model stand-in: a valid reply unless `bad` scripts one for (slot id, attempt)."""

    def __init__(self, bad: dict[tuple[str, int], str] | None = None) -> None:
        self.bad = bad or {}
        self.steps: list[Step] = []

    def __call__(self, step: Step) -> str:
        self.steps.append(step)
        scripted = self.bad.get((step.slot.id, step.attempt))
        return scripted if scripted is not None else json.dumps(valid_reply(step), ensure_ascii=False)
