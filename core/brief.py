"""The brief-builder: a code-built view of the story up to a position, from validated records.

A section worker for section N gets Position(N, 0), so nothing of its own section; a recap
at "ch 6 · scene 3" gets Position(6, 3). Records past the position are ignored even if
passed in. It never sees split text.
"""

from __future__ import annotations

from typing import NamedTuple

RECENT_SECTIONS = 3
CHAPTER0_LABEL = "chapter 0"


class Position(NamedTuple):
    order: int  # the section's running order; 0 = before section 1
    scene: int  # scenes 1..scene of that section are included; 0 = none


def _seen(label: str, scene: int, is_chapter0: bool) -> str:
    return label if is_chapter0 else f"{label} · scene {scene}"


def included_scenes(chapter0: dict | None, sections: list[tuple[int, str, dict]],
                    upto: Position) -> list[tuple[str, list[dict], bool]]:
    """(label, scenes, is_chapter0) for everything behind `upto`, in reading order.

    The one inclusion rule shared by the brief and the cut: chapter 0 once a story section
    has begun, every section before upto.order, and scenes 1..upto.scene of upto.order.
    A section with no included scene is left out; chapter 0 is labelled CHAPTER0_LABEL.
    """
    included: list[tuple[str, list[dict], bool]] = []
    if chapter0 is not None and upto.order >= 1:
        included.append((CHAPTER0_LABEL, chapter0["scenes"], True))
    for order, label, record in sorted(sections, key=lambda s: s[0]):
        if order < upto.order:
            scenes = record["scenes"]
        elif order == upto.order:
            scenes = [s for s in record["scenes"] if s["scene"] <= upto.scene]
        else:
            continue
        if scenes:
            included.append((label, scenes, False))
    return included


def build_brief(chapter0: dict | None, sections: list[tuple[int, str, dict]], upto: Position) -> dict:
    """{characters, glossary, recent, chapter0} over chapter 0 and everything before `upto`.

    `sections` items are (order, label, record). Chapter 0 counts only once a story section
    has begun (upto.order >= 1) and seeds the names for section 1.
    """
    included = included_scenes(chapter0, sections, upto)
    recent = [{"section": label, "scenes": [{"scene": s["scene"], "summary": s["summary"]} for s in scenes]}
              for label, scenes, is_chapter0 in included if not is_chapter0]

    characters: dict[str, dict] = {}
    glossary: dict[str, dict] = {}
    for label, scenes, is_chapter0 in included:
        for scene in scenes:
            for char in scene["characters"]:
                entry = characters.setdefault(char["name"], {"name": char["name"], "aka": []})
                if "aka" in char and char["aka"] not in entry["aka"]:
                    entry["aka"].append(char["aka"])
                entry["line"] = char["line"]
                entry["seen"] = _seen(label, scene["scene"], is_chapter0)
            for term in scene["terms"]:
                glossary[term["term"].casefold()] = {"term": term["term"], "meaning": term["meaning"]}

    return {
        "characters": sorted(characters.values(), key=lambda c: (c["name"].casefold(), c["name"])),
        "glossary": [glossary[k] for k in sorted(glossary)],
        "recent": recent[-RECENT_SECTIONS:],
        "chapter0": chapter0["scenes"][0]["summary"] if chapter0 is not None and upto.order >= 1 else None,
    }


def render_brief(brief: dict) -> str:
    """The brief as Markdown for a worker prompt or a recap."""
    parts: list[str] = []
    if brief["chapter0"]:
        parts.append(f"### Chapter 0\n\n{brief['chapter0']}")
    if brief["characters"]:
        lines = []
        for c in brief["characters"]:
            aka = f" (also: {', '.join(c['aka'])})" if c["aka"] else ""
            lines.append(f"- **{c['name']}**{aka}: {c['line']} ({c['seen']})")
        parts.append("### Characters\n\n" + "\n".join(lines))
    if brief["glossary"]:
        parts.append("### Glossary\n\n" + "\n".join(f"- **{g['term']}**: {g['meaning']}" for g in brief["glossary"]))
    if brief["recent"]:
        blocks = []
        for r in brief["recent"]:
            scenes = "\n".join(f"- scene {s['scene']}: {s['summary']}" for s in r["scenes"])
            blocks.append(f"#### {r['section']}\n\n{scenes}")
        parts.append("### Recent sections\n\n" + "\n\n".join(blocks))
    return "\n\n".join(parts) if parts else "(empty: this is the start of the book)"
