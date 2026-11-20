"""The brief-builder: a code-built view of the story up to a position, from validated records.

A section worker for section N gets Position(N, 0), so nothing of its own section; a recap
at "ch 6 · scene 3" gets Position(6, 3). Records past the position are ignored even if
passed in. It never sees split text.
"""

from __future__ import annotations

import re
from typing import NamedTuple

RECENT_SECTIONS = 3
RECAP_KEY_MAX = 8
CHAPTER0_LABEL = "chapter 0"
RECAP_WHOLE_BUDGET = 6000
RECAP_RECENT_BUDGET = 9000
EARLIER_NOTE = "(Some scene lines are left out here to fit; read the sections by name for more.)"

_SENTENCE_END = re.compile(r"[.!?][\"'”’»)]*(?=\s|$)")


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


def build_recap(chapter0: dict | None, sections: list[tuple[int, str, dict]], upto: Position) -> dict:
    """{summaries, recent, key, also}: the material a recap at `upto` is written from.

    `summaries` is every story section behind `upto` (chapter 0 left out); `recent` the last
    RECENT_SECTIONS of them in detail. `key` holds the brief's character entries for at most
    RECAP_KEY_MAX people who are the pov of a scene in `recent` or appear in 2+ of its
    sections, ranked by how many of those sections they appear in (a pov counts as appearing),
    ties by casefolded name then name; `also` every other name in `recent`, sorted.
    """
    story = [(label, scenes) for label, scenes, is_chapter0 in included_scenes(chapter0, sections, upto)
             if not is_chapter0]
    summaries = [{"section": label, "scenes": [{"scene": s["scene"], "summary": s["summary"]} for s in scenes]}
                 for label, scenes in story]
    recent = [{"section": label,
               "scenes": [{"scene": s["scene"], "pov": s["pov"], "summary": s["summary"], "events": s["events"]}
                          for s in scenes]}
              for label, scenes in story[-RECENT_SECTIONS:]]

    povs = {s["pov"] for _, scenes in story[-RECENT_SECTIONS:] for s in scenes if s["pov"]}
    sections_of: dict[str, int] = {}
    for _, scenes in story[-RECENT_SECTIONS:]:
        names = {c["name"] for s in scenes for c in s["characters"]} | {s["pov"] for s in scenes if s["pov"]}
        for name in names:
            sections_of[name] = sections_of.get(name, 0) + 1
    characters = build_brief(chapter0, sections, upto)["characters"]
    eligible = [c for c in characters if c["name"] in povs or sections_of.get(c["name"], 0) >= 2]
    key = sorted(eligible, key=lambda c: (-sections_of.get(c["name"], 0), c["name"].casefold(), c["name"]))
    key = key[:RECAP_KEY_MAX]
    keyed = {c["name"] for c in key}
    also = sorted((n for n in sections_of if n not in keyed), key=lambda n: (n.casefold(), n))
    return {"summaries": summaries, "recent": recent, "key": key, "also": also}


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


def _first_sentence(text: str) -> str:
    text = " ".join(text.split())
    end = _SENTENCE_END.search(text)
    return text[:end.end()] if end else text


def _cite(label: str, scene: int) -> str:
    return f"[{label} · {scene}]"


def _cite_seen(seen: str) -> str:
    """`seen` as a citation; a chapter-0 line has no story citation, so it is named plainly."""
    label, sep, scene = seen.rpartition(" · scene ")
    return _cite(label, int(scene)) if sep else f"({seen})"


def _earlier_block(summaries: list[dict]) -> str:
    """One first-sentence line per scene for `summaries`, within RECAP_WHOLE_BUDGET.

    Over budget, no section is dropped: each keeps its first scene line, and the room left
    is shared evenly in reading order (a section's unspent share passes to the next), each
    section keeping its following lines in order while its share lasts, then EARLIER_NOTE
    is added. If one line per section is already over budget, that is what is returned.
    """
    if not summaries:
        return ""
    lines = [[f"- {_cite(s['section'], c['scene'])} {_first_sentence(c['summary'])}" for c in s["scenes"]]
             for s in summaries]
    heads = [f"### {s['section']}\n\n" for s in summaries]

    def render(counts: list[int], note: bool) -> str:
        body = "\n\n".join(h + "\n".join(ls[:n]) for h, ls, n in zip(heads, lines, counts))
        return f"{body}\n\n{EARLIER_NOTE}" if note else body

    full = render([len(ls) for ls in lines], False)
    if len(full) <= RECAP_WHOLE_BUDGET:
        return full
    counts = [min(1, len(ls)) for ls in lines]
    room = RECAP_WHOLE_BUDGET - len(render(counts, True))
    for i, ls in enumerate(lines):
        share = room // (len(lines) - i)
        while counts[i] < len(ls) and len(ls[counts[i]]) + 1 <= share:
            spent = len(ls[counts[i]]) + 1
            share -= spent
            room -= spent
            counts[i] += 1
    return render(counts, True)


def render_recap(recap: dict, position_line: str) -> str:
    """The recap material as text the bot writes from: `position_line` first, then the whole
    story (first sentence per scene), the recent sections in full and who is in play. Every
    scene line carries its citation.

    Each block has its own budget. The whole-story block covers only the sections before the
    recent ones and is trimmed by _earlier_block to RECAP_WHOLE_BUDGET. The recent block
    (RECAP_RECENT_BUDGET) drops the event bullets of its oldest section first; once every
    event is gone it drops the oldest scenes, but always keeps the last one. Who is in play
    is never cut.
    """
    whole = _earlier_block(recap["summaries"][:len(recap["summaries"]) - len(recap["recent"])])

    def recent_block(events_from: int, scenes_dropped: int) -> str:
        sections = []
        for i, r in enumerate(recap["recent"]):
            lines = []
            for c in r["scenes"]:
                if scenes_dropped > 0:
                    scenes_dropped -= 1
                    continue
                pov = f"(pov: {c['pov']}) " if c["pov"] else ""
                lines.append(f"- {_cite(r['section'], c['scene'])} {pov}{c['summary']}")
                if i >= events_from:
                    lines += [f"  - {e}" for e in c["events"]]
            if lines:
                sections.append(f"### {r['section']}\n\n" + "\n".join(lines))
        return "\n\n".join(sections)

    scene_count = sum(len(r["scenes"]) for r in recap["recent"])
    attempts = [(i, 0) for i in range(len(recap["recent"]) + 1)]
    attempts += [(len(recap["recent"]), n) for n in range(1, scene_count)]
    recent = next((b for b in (recent_block(*a) for a in attempts) if len(b) <= RECAP_RECENT_BUDGET),
                  recent_block(len(recap["recent"]), max(scene_count - 1, 0)))

    who = []
    for c in recap["key"]:
        aka = f" (also: {', '.join(c['aka'])})" if c["aka"] else ""
        who.append(f"- **{c['name']}**{aka}: {c['line']} {_cite_seen(c['seen'])}")
    if recap["also"]:
        who.append(f"Also around: {', '.join(recap['also'])}")

    parts = [position_line,
             "## The whole story, one line per scene\n\n" + whole,
             "## The latest sections in full\n\n" + recent,
             "## Who is in play\n\n" + "\n".join(who)]
    return "\n\n".join(parts) + "\n"
