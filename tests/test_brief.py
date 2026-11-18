import json

from core.brief import RECENT_SECTIONS, Position, build_brief, render_brief


def char(name, line, new=False, **aka):
    return {"name": name, "line": line, "new": new, **aka}


def scene(k, summary, characters=(), terms=()):
    return {"scene": k, "pov": None, "summary": summary, "events": ["e"], "characters": list(characters),
            "places": [], "terms": list(terms), "open_questions": []}


def record(label, *scenes):
    return {"section": label, "scenes": list(scenes)}


CH0 = record("chapter 0", scene(1, "The cast of the docks.", [char("Orrin Vale", "lamplighter", True)]))
SECTIONS = [
    (1, "ch 1", record("ch 1", scene(1, "Tamsin arrives quillfeather.", [char("Tamsin Rook", "a courier", True)]),
                       scene(2, "Orrin talks.", [char("Orrin Vale", "talks to Tamsin")]))),
    (2, "ch 2", record("ch 2", scene(1, "Corvin brambleglass.", [char("Corvin Ashby", "a lens grinder", True)],
                                     [{"term": "Glass", "meaning": "a lens"}]))),
    (3, "ch 3", record("ch 3",
                       scene(1, "Ilse thornwick one.", [char("Ilse Marrow", "a rope seller", True,
                                                             aka="the salt widow", quote="q")]),
                       scene(2, "Ilse thornwick two.", [char("Ilse Marrow", "counts knots", aka="Mother Rope",
                                                             quote="q")],
                             [{"term": "glass", "meaning": "a mirror"}]),
                       scene(3, "Ilse zephyrfall leaves.", [char("Ilse Marrow", "has left zephyrfall"),
                                                           char("Nell Quarry", "zephyrfall stranger", True)]))),
]


def text_of(brief):
    return json.dumps(brief, ensure_ascii=False) + render_brief(brief)


def test_future_records_never_reach_the_brief():
    for order in range(1, 4):
        brief = build_brief(CH0, SECTIONS, Position(order, 0))
        later = [json.dumps(r) for o, _, r in SECTIONS if o >= order]
        out = text_of(brief)
        for marker in ("quillfeather", "brambleglass", "thornwick", "zephyrfall"):
            if any(marker in r for r in later):
                assert marker not in out, (order, marker)


def test_mid_section_position_includes_scenes_up_to_it_only():
    brief = build_brief(CH0, SECTIONS, Position(3, 2))
    out = text_of(brief)
    assert "Ilse thornwick two." in out and "zephyrfall" not in out
    ilse = next(c for c in brief["characters"] if c["name"] == "Ilse Marrow")
    assert ilse == {"name": "Ilse Marrow", "aka": ["the salt widow", "Mother Rope"], "line": "counts knots",
                    "seen": "ch 3 · scene 2"}
    assert brief["recent"][-1] == {"section": "ch 3", "scenes": [{"scene": 1, "summary": "Ilse thornwick one."},
                                                                 {"scene": 2, "summary": "Ilse thornwick two."}]}


def test_chapter_0_seeds_section_1_and_is_its_own_block():
    brief = build_brief(CH0, SECTIONS, Position(1, 0))
    assert brief["characters"] == [{"name": "Orrin Vale", "aka": [], "line": "lamplighter", "seen": "chapter 0"}]
    assert brief["chapter0"] == "The cast of the docks."
    assert brief["recent"] == [] and brief["glossary"] == []
    assert "### Chapter 0\n\nThe cast of the docks." in render_brief(brief)


def test_nothing_before_section_1():
    brief = build_brief(CH0, SECTIONS, Position(0, 0))
    assert brief == {"characters": [], "glossary": [], "recent": [], "chapter0": None}
    assert render_brief(brief) == "(empty: this is the start of the book)"


def test_latest_line_wins_and_aliases_accumulate():
    brief = build_brief(CH0, SECTIONS, Position(4, 0))
    by_name = {c["name"]: c for c in brief["characters"]}
    assert by_name["Orrin Vale"]["line"] == "talks to Tamsin" and by_name["Orrin Vale"]["seen"] == "ch 1 · scene 2"
    assert by_name["Ilse Marrow"]["aka"] == ["the salt widow", "Mother Rope"]
    assert by_name["Ilse Marrow"]["line"] == "has left zephyrfall"
    assert [c["name"] for c in brief["characters"]] == sorted(by_name)


def test_glossary_keeps_latest_meaning_and_spelling():
    brief = build_brief(CH0, SECTIONS, Position(4, 0))
    assert brief["glossary"] == [{"term": "glass", "meaning": "a mirror"}]


def test_a_re_listed_term_shows_the_newest_meaning_reached_by_the_position():
    assert build_brief(CH0, SECTIONS, Position(3, 1))["glossary"] == [{"term": "Glass", "meaning": "a lens"}]
    assert build_brief(CH0, SECTIONS, Position(3, 2))["glossary"] == [{"term": "glass", "meaning": "a mirror"}]


def test_recent_is_the_last_sections_from_scene_summaries_without_chapter_0():
    brief = build_brief(CH0, SECTIONS, Position(3, 1))
    assert RECENT_SECTIONS == 3
    assert [r["section"] for r in brief["recent"]] == ["ch 1", "ch 2", "ch 3"]
    assert brief["recent"][0]["scenes"] == [{"scene": 1, "summary": "Tamsin arrives quillfeather."},
                                            {"scene": 2, "summary": "Orrin talks."}]
    extra = SECTIONS + [(4, "ch 4", record("ch 4", scene(1, "Four.")))]
    assert [r["section"] for r in build_brief(CH0, extra, Position(5, 0))["recent"]] == ["ch 2", "ch 3", "ch 4"]


def test_render_lists_characters_glossary_and_recent():
    out = render_brief(build_brief(CH0, SECTIONS, Position(3, 1)))
    assert "- **Ilse Marrow** (also: the salt widow): a rope seller (ch 3 · scene 1)" in out
    assert "- **Glass**: a lens" in out
    assert "#### ch 2\n\n- scene 1: Corvin brambleglass." in out
