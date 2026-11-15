import json

import pytest

from core.brief import Position, build_brief, included_scenes
from core.merge import chapter0_tag, merge, render_characters, render_glossary, render_timeline, tag
from tests.test_brief import CH0, SECTIONS, char, record, scene

LABELS = ["Cast"]


def text_of(merged):
    return (json.dumps(merged, ensure_ascii=False) + render_characters(merged)
            + render_glossary(merged) + render_timeline(merged))


def person(merged, name):
    return next(c for c in merged["characters"] if c["name"] == name)


def test_tags_are_section_plus_scene_or_the_printed_chapter_0_labels():
    assert tag("ch 6", 2) == "[ch 6 · 2]"
    assert tag("§12", 1) == "[§12 · 1]"
    assert chapter0_tag(["Dramatis Personae"]) == "[Dramatis Personae]"
    assert chapter0_tag(["Dramatis Personae", "Glossary"]) == "[Dramatis Personae] [Glossary]"


def test_characters_stack_one_tagged_entry_per_scene():
    merged = merge(CH0, SECTIONS, Position(3, 3), LABELS)
    assert person(merged, "Orrin Vale")["entries"] == [{"at": "[Cast]", "line": "lamplighter"},
                                                      {"at": "[ch 1 · 2]", "line": "talks to Tamsin"}]
    assert [e["at"] for e in person(merged, "Ilse Marrow")["entries"]] == ["[ch 3 · 1]", "[ch 3 · 2]", "[ch 3 · 3]"]
    assert [c["name"] for c in merged["characters"]] == ["Corvin Ashby", "Ilse Marrow", "Nell Quarry", "Orrin Vale",
                                                         "Tamsin Rook"]
    assert ("## Ilse Marrow\n"
            "- [ch 3 · 1] a rope seller\n"
            "- [ch 3 · 1] revealed as \"the salt widow\"\n"
            "- [ch 3 · 2] counts knots\n"
            "- [ch 3 · 2] revealed as \"Mother Rope\"\n"
            "- [ch 3 · 3] has left zephyrfall\n") in render_characters(merged)


def test_an_alias_is_tagged_with_the_scene_it_first_appears_in():
    sections = SECTIONS + [(4, "ch 4", record("ch 4", scene(1, "Ilse again.", [
        char("Ilse Marrow", "returns", aka="the salt widow", quote="q")])))]
    ilse = person(merge(CH0, sections, Position(4, 1), LABELS), "Ilse Marrow")
    assert ilse["aka"] == [{"alias": "the salt widow", "at": "[ch 3 · 1]"},
                           {"alias": "Mother Rope", "at": "[ch 3 · 2]"}]
    assert ilse["entries"][-1] == {"at": "[ch 4 · 1]", "line": "returns"}


def test_timeline_keeps_one_line_per_scene_and_stops_at_the_position():
    merged = merge(CH0, SECTIONS, Position(3, 2), LABELS)
    assert [(t["section"], t["at_chapter0"]) for t in merged["timeline"]] == [
        ("Cast", True), ("ch 1", False), ("ch 2", False), ("ch 3", False)]
    assert [s["at"] for t in merged["timeline"] for s in t["scenes"]] == [
        "[Cast]", "[ch 1 · 1]", "[ch 1 · 2]", "[ch 2 · 1]", "[ch 3 · 1]", "[ch 3 · 2]"]
    assert merged["timeline"][1]["scenes"][0] == {"at": "[ch 1 · 1]", "summary": "Tamsin arrives quillfeather.",
                                                  "events": ["e"]}
    assert "## ch 1\n- [ch 1 · 1] Tamsin arrives quillfeather.\n  - e\n- [ch 1 · 2] Orrin talks.\n  - e\n" \
        in render_timeline(merged)


def test_glossary_keeps_every_meaning_oldest_first_without_consecutive_repeats():
    sections = [
        (1, "ch 1", record("ch 1",
                           scene(1, "a", terms=[{"term": "Circle", "meaning": "a secret society"}]),
                           scene(2, "b", terms=[{"term": "Circle", "meaning": "a secret society"}]),
                           scene(3, "c", terms=[{"term": "circle", "meaning": "also hires Bondless"}]))),
        (2, "ch 2", record("ch 2", scene(1, "d", terms=[{"term": "Circle", "meaning": "a secret society"}]))),
    ]
    merged = merge(None, sections, Position(2, 1), [])
    assert merged["glossary"] == [{"term": "Circle", "meanings": [
        {"meaning": "a secret society", "at": "[ch 1 · 1]"},
        {"meaning": "also hires Bondless", "at": "[ch 1 · 3]"},
        {"meaning": "a secret society", "at": "[ch 2 · 1]"},
    ]}]
    assert render_glossary(merged) == ("# Glossary\n\n## Circle\n- [ch 1 · 1] a secret society\n"
                                       "- [ch 1 · 3] also hires Bondless\n- [ch 2 · 1] a secret society\n")


def test_chapter_0_entries_carry_every_printed_label():
    merged = merge(CH0, SECTIONS, Position(1, 1), ["Dramatis Personae", "Glossary"])
    assert person(merged, "Orrin Vale")["entries"][0]["at"] == "[Dramatis Personae] [Glossary]"
    assert merged["timeline"][0]["at_chapter0"] is True
    assert merged["timeline"][0]["scenes"][0]["at"] == "[Dramatis Personae] [Glossary]"
    assert "chapter 0" not in text_of(merged)


def test_nothing_before_section_1():
    assert merge(CH0, SECTIONS, Position(0, 0), LABELS) == {"characters": [], "glossary": [], "timeline": []}


def test_chapter_0_without_its_labels_is_refused():
    with pytest.raises(ValueError, match="chapter-0 labels"):
        merge(CH0, SECTIONS, Position(1, 1), [])


def test_future_records_never_reach_the_merge():
    for order in range(1, 4):
        out = text_of(merge(CH0, SECTIONS, Position(order, 0), LABELS))
        later = [json.dumps(r) for o, _, r in SECTIONS if o >= order]
        for marker in ("quillfeather", "brambleglass", "thornwick", "zephyrfall"):
            if any(marker in r for r in later):
                assert marker not in out, (order, marker)
    assert "zephyrfall" not in text_of(merge(CH0, SECTIONS, Position(3, 2), LABELS))


def test_merge_and_brief_include_the_same_scenes_at_every_position():
    for order in range(0, 4):
        for k in range(0, 4):
            upto = Position(order, k)
            merged = merge(CH0, SECTIONS, upto, LABELS)
            brief = build_brief(CH0, SECTIONS, upto)
            assert len([s for t in merged["timeline"] for s in t["scenes"]]) == sum(
                len(scenes) for _, scenes, _ in included_scenes(CH0, SECTIONS, upto)), upto
            assert {c["name"]: c["entries"][-1]["line"] for c in merged["characters"]} == {
                c["name"]: c["line"] for c in brief["characters"]}, upto
            assert {c["name"]: [a["alias"] for a in c["aka"]] for c in merged["characters"]} == {
                c["name"]: c["aka"] for c in brief["characters"]}, upto
            assert [(g["term"], g["meanings"][-1]["meaning"]) for g in merged["glossary"]] == [
                (g["term"], g["meaning"]) for g in brief["glossary"]], upto


def test_included_scenes_is_reading_order_with_chapter_0_first():
    assert [(label, [s["scene"] for s in scenes], ch0) for label, scenes, ch0
            in included_scenes(CH0, SECTIONS, Position(3, 2))] == [
        ("chapter 0", [1], True), ("ch 1", [1, 2], False), ("ch 2", [1], False), ("ch 3", [1, 2], False)]
    assert included_scenes(CH0, SECTIONS, Position(0, 0)) == []
    assert [label for label, _, _ in included_scenes(CH0, SECTIONS, Position(2, 0))] == ["chapter 0", "ch 1"]
