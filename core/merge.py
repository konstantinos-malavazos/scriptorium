"""The merge: characters, glossary and story timeline over the scenes behind a position.

Pure code over validated records, no model and no I/O. Inclusion is `included_scenes`, the
same rule as the brief. Every entry carries its citation tag: `[ch 6 · 2]` for a story
scene, the printed chapter-0 labels (`[Dramatis Personae]`) for chapter 0. `tag` and
`chapter0_tag` are the only places that form is built.
"""

from __future__ import annotations

from core.brief import Position, included_scenes


def tag(label: str, scene: int) -> str:
    return f"[{label} · {scene}]"


def chapter0_tag(labels: list[str]) -> str:
    """One bracket per printed unit: the worker sees chapter 0 as one scene, so a fact in it
    cannot be traced to a single unit."""
    return " ".join(f"[{label}]" for label in labels)


def merge(chapter0: dict | None, sections: list[tuple[int, str, dict]], upto: Position,
          chapter0_labels: list[str]) -> dict:
    """{characters, glossary, timeline} over chapter 0 and everything before `upto`.

    `sections` items are (order, label, record); `chapter0_labels` are the printed chapter-0
    unit labels in split order. Raises ValueError if chapter 0 is included without them.
    """
    characters: dict[str, dict] = {}
    glossary: dict[str, dict] = {}
    timeline: list[dict] = []
    for label, scenes, is_chapter0 in included_scenes(chapter0, sections, upto):
        if is_chapter0 and not chapter0_labels:
            raise ValueError("chapter 0 is included but no chapter-0 labels were given")
        section = {"section": ", ".join(chapter0_labels) if is_chapter0 else label,
                   "at_chapter0": is_chapter0, "scenes": []}
        timeline.append(section)
        for scene in scenes:
            at = chapter0_tag(chapter0_labels) if is_chapter0 else tag(label, scene["scene"])
            section["scenes"].append({"at": at, "summary": scene["summary"], "events": list(scene["events"])})
            for char in scene["characters"]:
                entry = characters.setdefault(char["name"], {"name": char["name"], "entries": [], "aka": []})
                entry["entries"].append({"at": at, "line": char["line"]})
                if "aka" in char and all(a["alias"] != char["aka"] for a in entry["aka"]):
                    entry["aka"].append({"alias": char["aka"], "at": at})
            for term in scene["terms"]:
                entry = glossary.setdefault(term["term"].casefold(), {"term": term["term"], "meanings": []})
                entry["term"] = term["term"]
                if not entry["meanings"] or entry["meanings"][-1]["meaning"] != term["meaning"]:
                    entry["meanings"].append({"meaning": term["meaning"], "at": at})
    return {
        "characters": sorted(characters.values(), key=lambda c: (c["name"].casefold(), c["name"])),
        "glossary": [glossary[k] for k in sorted(glossary)],
        "timeline": timeline,
    }


def render_characters(merged: dict) -> str:
    """One timeline per character; an alias line follows the entry of the scene it first appears in."""
    blocks = []
    for c in merged["characters"]:
        lines = [f"## {c['name']}"]
        pending = list(c["aka"])
        for e in c["entries"]:
            lines.append(f"- {e['at']} {e['line']}")
            while pending and pending[0]["at"] == e["at"]:
                lines.append(f"- {e['at']} revealed as \"{pending.pop(0)['alias']}\"")
        blocks.append("\n".join(lines) + "\n")
    return "# Characters\n\n" + "\n".join(blocks)


def render_glossary(merged: dict) -> str:
    blocks = []
    for g in merged["glossary"]:
        lines = [f"## {g['term']}", *(f"- {m['at']} {m['meaning']}" for m in g["meanings"])]
        blocks.append("\n".join(lines) + "\n")
    return "# Glossary\n\n" + "\n".join(blocks)


def render_timeline(merged: dict) -> str:
    blocks = []
    for t in merged["timeline"]:
        lines = [f"## {t['section']}"]
        for s in t["scenes"]:
            lines.append(f"- {s['at']} {s['summary']}")
            lines.extend(f"  - {event}" for event in s["events"])
        blocks.append("\n".join(lines) + "\n")
    return "# Timeline\n\n" + "\n".join(blocks)
