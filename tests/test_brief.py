import json

from core.brief import (EARLIER_NOTE, RECAP_KEY_MAX, RECAP_RECENT_BUDGET, RECAP_WHOLE_BUDGET, RECENT_SECTIONS,
                        Position, build_brief, build_recap, render_brief, render_recap)


def char(name, line, new=False, **aka):
    return {"name": name, "line": line, "new": new, **aka}


def scene(k, summary, characters=(), terms=(), pov=None):
    return {"scene": k, "pov": pov, "summary": summary, "events": ["e"], "characters": list(characters),
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


# --- recap ---

RECAP_SECTIONS = [
    (1, "ch 1", record("ch 1", scene(1, "Tamsin crosses. She waits.", [char("Tamsin Rook", "a courier", True)],
                                     pov="Tamsin Rook"))),
    (2, "ch 2", record("ch 2", scene(1, "Orrin and Corvin meet.", [char("Orrin Vale", "talks", True),
                                                                   char("Corvin Ashby", "grinds lenses", True)]))),
    (3, "ch 3", record("ch 3", scene(1, "Ilse sells rope.", [char("Ilse Marrow", "a rope seller", True)],
                                     pov="Ilse Marrow"),
                       scene(2, "Orrin waits.", [char("Orrin Vale", "waits at the dock")]))),
    (4, "ch 4", record("ch 4", scene(1, "Nell arrives.", [char("Nell Quarry", "a stranger", True)]),
                       scene(2, "Orrin leaves wolfsbane.", [char("Orrin Vale", "leaves wolfsbane"),
                                                            char("Corvin Ashby", "follows")]))),
]


def recap_at(order, scene_k):
    return build_recap(CH0, RECAP_SECTIONS, Position(order, scene_k))


def test_recap_key_characters_are_recent_povs_or_in_two_recent_sections():
    recap = recap_at(4, 1)
    assert [r["section"] for r in recap["recent"]] == ["ch 2", "ch 3", "ch 4"]
    assert [c["name"] for c in recap["key"]] == ["Orrin Vale", "Ilse Marrow"]
    assert recap["also"] == ["Corvin Ashby", "Nell Quarry"]


def test_recap_key_keeps_at_most_eight_ranked_by_recent_sections_then_name():
    assert RECAP_KEY_MAX == 8
    names = [f"Person {c}" for c in "ABCDEFGHIJ"]
    sections = [
        (1, "ch 1", record("ch 1", scene(1, "One.", [char(n, "l", True) for n in names]))),
        (2, "ch 2", record("ch 2", scene(1, "Two.", [char(n, "l") for n in names[2:]] + [char("Zed", "l", True)],
                                         pov="Person B"))),
        (3, "ch 3", record("ch 3", scene(1, "Three.", [char(n, "l") for n in names[4:]]))),
    ]
    recap = build_recap(None, sections, Position(4, 0))
    # E..J appear in 3 sections; C, D in 2; B in 2 (a pov counts as appearing); A once, Zed once.
    assert [c["name"] for c in recap["key"]] == [f"Person {c}" for c in "EFGHIJ"] + ["Person B", "Person C"]
    assert recap["also"] == ["Person A", "Person D", "Zed"]
    assert len(recap["key"]) == RECAP_KEY_MAX


def test_recap_key_line_is_the_latest_at_or_before_the_scene():
    orrin = next(c for c in recap_at(4, 1)["key"] if c["name"] == "Orrin Vale")
    assert orrin == {"name": "Orrin Vale", "aka": [], "line": "waits at the dock", "seen": "ch 3 · scene 2"}


def test_recap_stops_at_the_readers_scene():
    recap = recap_at(4, 1)
    assert recap["recent"][-1] == {"section": "ch 4", "scenes": [
        {"scene": 1, "pov": None, "summary": "Nell arrives.", "events": ["e"]}]}
    assert "wolfsbane" not in json.dumps(recap) + render_recap(recap, "Up to ch 4 · scene 1:")


def test_recap_with_fewer_than_three_sections_behind_holds_what_there_is():
    recap = recap_at(1, 1)
    assert [r["section"] for r in recap["recent"]] == ["ch 1"]
    assert [c["name"] for c in recap["key"]] == ["Tamsin Rook"] and recap["also"] == []


def test_recap_summaries_cover_every_story_section_without_chapter_0():
    recap = recap_at(4, 1)
    assert [s["section"] for s in recap["summaries"]] == ["ch 1", "ch 2", "ch 3", "ch 4"]
    assert recap["summaries"][0] == {"section": "ch 1", "scenes": [{"scene": 1, "summary": "Tamsin crosses. She waits."}]}
    assert "Orrin Vale" not in recap["also"] and "The cast of the docks." not in json.dumps(recap)


def test_render_recap_opens_with_the_position_line_and_cites_every_scene_line():
    out = render_recap(recap_at(4, 1), "Up to ch 4 · scene 1:")
    assert out.splitlines()[0] == "Up to ch 4 · scene 1:"
    assert "- [ch 1 · 1] Tamsin crosses.\n" in out
    assert "- [ch 3 · 1] (pov: Ilse Marrow) Ilse sells rope.\n  - e" in out
    assert "- **Orrin Vale**: waits at the dock [ch 3 · 2]" in out
    assert "Also around: Corvin Ashby, Nell Quarry" in out
    assert "so far" not in out


def split_blocks(out):
    whole, rest = out.split("## The latest sections in full\n\n")
    recent, who = rest.split("## Who is in play\n\n")
    return whole.split("one line per scene\n\n")[1].rstrip("\n"), recent.rstrip("\n"), who


def test_render_recap_whole_story_block_covers_only_the_sections_before_the_recent_ones():
    assert (RECAP_WHOLE_BUDGET, RECAP_RECENT_BUDGET) == (6000, 9000)
    whole, recent, _ = split_blocks(render_recap(recap_at(4, 1), "Up to ch 4 · scene 1:"))
    assert whole == "### ch 1\n\n- [ch 1 · 1] Tamsin crosses."
    assert all(f"### ch {o}" in recent for o in (2, 3, 4)) and "### ch 1" not in recent


def test_render_recap_over_budget_keeps_every_earlier_section_and_shares_the_room_evenly():
    def sec(o, n):
        return (o, f"ch {o}", record(f"ch {o}", *[scene(k, f"S{o}k{k} " + "word " * 40 + ".") for k in range(1, n + 1)]))
    sections = [sec(o, 6) for o in range(1, 11)] + [sec(o, 1) for o in (11, 12, 13)]
    out = render_recap(build_recap(None, sections, Position(14, 0)), "Up to ch 14 · scene 0:")
    whole, _, _ = split_blocks(out)
    assert len(whole) <= RECAP_WHOLE_BUDGET and whole.endswith(EARLIER_NOTE)
    kept = [[k for k in range(1, 7) if f"S{o}k{k} " in whole] for o in range(1, 11)]
    assert all(f"### ch {o}\n\n" in whole for o in range(1, 11))
    assert all(ks == list(range(1, len(ks) + 1)) and ks for ks in kept)
    assert max(map(len, kept)) - min(map(len, kept)) <= 1 and max(map(len, kept)) < 6
    assert "S11k1 " not in whole


def test_render_recap_keeps_one_line_per_earlier_section_even_when_that_is_over_budget():
    def sec(o):
        return (o, f"ch {o}", record(f"ch {o}", scene(1, f"S{o}k1 " + "word " * 100 + "."),
                                     scene(2, f"S{o}k2 short.")))
    sections = [sec(o) for o in range(1, 24)]
    whole, _, _ = split_blocks(render_recap(build_recap(None, sections, Position(24, 0)), "Up to ch 24 · scene 0:"))
    assert all(f"S{o}k1 " in whole and f"S{o}k2 " not in whole for o in range(1, 21))
    assert len(whole) > RECAP_WHOLE_BUDGET and whole.endswith(EARLIER_NOTE)


def test_render_recap_cuts_event_bullets_oldest_section_first_and_keeps_the_whole_story_block():
    def big(o):
        sc = scene(1, f"Section{o} summary.", [char("Hero", "a hero", True)])
        sc["events"] = [f"Event{o}-{i} " + "word " * 40 for i in range(20)]
        return (o, f"ch {o}", record(f"ch {o}", sc))
    recap = build_recap(None, [big(o) for o in range(1, 8)], Position(8, 0))
    assert sum(len(e) for r in recap["recent"] for c in r["scenes"] for e in c["events"]) > RECAP_RECENT_BUDGET
    out = render_recap(recap, "Up to ch 8 · scene 0:")
    whole, recent, who = split_blocks(out)
    assert out.splitlines()[0] == "Up to ch 8 · scene 0:"
    assert len(recent) <= RECAP_RECENT_BUDGET and len(whole) <= RECAP_WHOLE_BUDGET
    assert "Section4 summary." in whole and "Section1 summary." in whole and "Section5 summary." not in whole
    assert all(f"Section{o} summary." in recent for o in (5, 6, 7))
    assert "Event5-0 " not in recent and "Event7-19 " in recent
    assert "- **Hero**" in who


def test_render_recap_drops_the_oldest_recent_scenes_when_summaries_alone_exceed_the_budget():
    def sec(o):
        return (o, f"ch {o}", record(f"ch {o}", *[scene(k, f"S{o}k{k} " + "word " * 200 + ".") for k in range(1, 6)]))
    recap = build_recap(None, [sec(o) for o in range(1, 6)], Position(6, 0))
    out = render_recap(recap, "Up to ch 6 · scene 0:")
    whole, recent, _ = split_blocks(out)
    assert len(recent) <= RECAP_RECENT_BUDGET and len(whole) <= RECAP_WHOLE_BUDGET
    assert "S5k5 " in recent and "S3k1 " not in recent and "### ch 3" not in recent
    assert whole.strip() and "### ch 2" in whole and "### ch 3" not in whole
