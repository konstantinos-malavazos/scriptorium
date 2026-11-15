import copy
import json

import pytest

from core.extract import slots
from core.split import load_approved
from core.validate import SUMMARY_MAX_WORDS, normalise, parse_reply, validate
from tests.index_fixture import fixture_split, write_split

BRIEF = {
    "characters": [{"name": "Marta", "aka": ["the key-keeper"], "line": "has the key", "seen": "ch 1 · scene 1"}],
    "glossary": [{"term": "Scrapper", "meaning": "a scavenger kid"}],
    "recent": [],
    "chapter0": None,
}
TEXTS = [
    "Marta gave the key to the grey-coated man, whom she called “Kell the\nquiet”.",
    "Kell climbed the water tower.",
]


def scene(k, **over):
    s = {"scene": k, "pov": "Marta", "summary": "Marta hands over the key and Kell climbs the tower.",
         "events": ["Marta gives Kell the key"],
         "characters": [{"name": "Marta", "line": "trusts Kell", "new": False},
                        {"name": "Kell", "line": "takes the key", "new": True}],
         "places": ["water tower"], "terms": [], "open_questions": []}
    s.update(over)
    return s


def good():
    return {"section": "ch 2", "scenes": [scene(1), scene(2)]}


def check(reply, *, texts=TEXTS, chapter0=False, section="ch 2"):
    text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
    return validate(text, section=section, scene_texts=texts, brief=BRIEF, chapter0=chapter0)


def rejected(reply, needle, **kw):
    record, errors, warnings = check(reply, **kw)
    assert record is None and warnings == []
    assert any(needle in e for e in errors), errors
    return errors


def test_clean_reply_is_accepted_with_no_warnings():
    record, errors, warnings = check(good())
    assert (record, errors, warnings) == (good(), [], [])


def test_one_json_fence_is_unwrapped():
    record, errors, _ = check("```json\n" + json.dumps(good()) + "\n```\n")
    assert record == good() and errors == []


@pytest.mark.parametrize("text", ["not json", "Here it is: " + json.dumps(good()),
                                  "```\n" + json.dumps(good()) + "\n```", json.dumps(good()) + "\nthanks"])
def test_invalid_json_or_prose_around_it_is_rejected(text):
    rejected(text, "not valid JSON")


def test_top_level_must_be_an_object():
    assert parse_reply("[1, 2]") == (None, ["the reply must be one JSON object"])


def test_missing_and_unknown_top_fields_are_rejected():
    rejected({"scenes": good()["scenes"]}, 'missing field "section"')
    rejected({**good(), "summary": "x"}, 'unknown field "summary"')


def test_missing_and_unknown_scene_fields_are_rejected():
    r = good()
    del r["scenes"][1]["places"]
    rejected(r, 'scene 2: missing field "places"')
    r = good()
    r["scenes"][0]["mood"] = "grim"
    rejected(r, 'scene 1: unknown field "mood"')


def test_unknown_character_and_term_fields_are_rejected():
    r = good()
    r["scenes"][0]["characters"][0]["age"] = 40
    rejected(r, 'scene 1: characters[0]: unknown field "age"')
    r = good()
    r["scenes"][0]["terms"] = [{"term": "lift", "meaning": "an elevator", "note": "x"}]
    rejected(r, 'scene 1: terms[0]: unknown field "note"')


@pytest.mark.parametrize("path,value,needle", [
    (("scenes", 0, "scene"), True, '"scene" must be an integer'),
    (("scenes", 0, "characters", 0, "new"), 0, '"new" must be true or false'),
    (("scenes", 0, "pov"), 3, '"pov" must be a name or null'),
    (("scenes", 0, "events"), [1], '"events" must be a list of strings'),
    (("scenes", 0, "characters"), {}, '"characters" must be a list'),
    (("scenes", 0, "summary"), None, '"summary" must be a string'),
    (("scenes", 0, "terms"), [{"term": "lift", "meaning": 2}], '"meaning" must be a string'),
])
def test_wrong_types_are_rejected(path, value, needle):
    r = good()
    target = r
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    rejected(r, needle)


def test_null_pov_is_allowed():
    r = good()
    r["scenes"][0]["pov"] = None
    assert check(r)[1] == []


def test_empty_name_term_aka_or_quote_is_rejected():
    r = good()
    r["scenes"][0]["characters"][0]["name"] = "  "
    rejected(r, '"name" is empty')
    r = good()
    r["scenes"][0]["terms"] = [{"term": "", "meaning": "m"}]
    rejected(r, '"term" is empty')
    r = good()
    r["scenes"][0]["characters"][1].update(aka="", quote="Kell")
    rejected(r, '"aka" is empty')
    r = good()
    r["scenes"][0]["characters"][1].update(aka="the quiet one", quote=" ")
    rejected(r, '"quote" is empty')


def test_aka_without_quote_is_rejected():
    r = good()
    r["scenes"][0]["characters"][1]["aka"] = "Kell the quiet"
    rejected(r, '"aka" and "quote" go together')


def test_section_must_match_the_slot():
    rejected({**good(), "section": "Chapter 2"}, '"section" is "Chapter 2"; expected "ch 2"')


def test_scene_numbers_must_be_one_to_k_in_order():
    r = good()
    r["scenes"][1]["scene"] = 3
    rejected(r, "scenes must be numbered [1, 2] in order; got [1, 3]")
    rejected({"section": "ch 2", "scenes": [scene(1)]}, "got [1]")
    rejected({"section": "ch 2", "scenes": [scene(2), scene(1)]}, "got [2, 1]")


def test_empty_summary_or_events_is_rejected_outside_chapter_0():
    r = good()
    r["scenes"][1]["summary"] = " \n"
    rejected(r, 'scene 2: "summary" is empty')
    r = good()
    r["scenes"][0]["events"] = []
    rejected(r, 'scene 1: "events" is empty')


def test_chapter_0_is_exempt_from_summary_and_events_only():
    r = {"section": "chapter 0", "scenes": [scene(1, summary="", events=[])]}
    record, errors, warnings = check(r, texts=TEXTS[:1], chapter0=True, section="chapter 0")
    assert record == r and errors == [] and warnings == []
    r["scenes"][0]["characters"][1]["new"] = False
    rejected(r, "set \"new\": true", texts=TEXTS[:1], chapter0=True, section="chapter 0")


def test_brief_name_must_not_be_marked_new():
    r = good()
    r["scenes"][0]["characters"][0]["new"] = True
    rejected(r, 'scene 1: characters[0]: "Marta" is in the brief; set "new": false')


def test_alias_used_as_a_name_is_rejected_with_the_canonical_name():
    r = good()
    r["scenes"][0]["characters"][0]["name"] = "the key-keeper"
    for new in (True, False):
        r["scenes"][0]["characters"][0]["new"] = new
        rejected(r, "\"the key-keeper\" is an alias in the brief; use the brief's name \"Marta\"")


def test_name_not_in_brief_must_be_new_in_every_scene():
    r = good()
    r["scenes"][1]["characters"][1]["new"] = False
    errors = rejected(r, 'scene 2: characters[1]: "Kell" is not in the brief; set "new": true')
    assert len(errors) == 1


def test_quote_must_be_in_its_scene_text():
    r = good()
    r["scenes"][0]["characters"][1].update(aka="Kell the bold", quote="Kell the bold")
    rejected(r, 'scene 1: characters[1]: "quote" is not in this scene\'s text')


def test_quote_from_another_scene_is_rejected():
    r = good()
    r["scenes"][1]["characters"][1].update(aka="the grey-coated man", quote="the grey-coated man")
    rejected(r, 'scene 2: characters[1]: "quote" is not in this scene\'s text')


def test_quote_matches_across_straight_quotes_and_a_line_break():
    r = good()
    r["scenes"][0]["characters"][1].update(aka="Kell the quiet", quote='called "Kell the quiet"')
    assert check(r)[1] == []


def test_quote_keeps_case():
    r = good()
    r["scenes"][0]["characters"][1].update(aka="kell", quote="KELL THE QUIET")
    rejected(r, '"quote" is not in this scene\'s text')


def test_normalise_folds_quote_marks_and_whitespace():
    assert normalise("“a” ‘b’\n\t c′ ″") == "\"a\" 'b' c' \""


def test_heading_counts_in_scene_1_quote_text(tmp_path):
    write_split(tmp_path, "fx", fixture_split())
    ch2 = next(s for s in slots(load_approved(tmp_path, "fx")) if s.section == "ch 2")
    reply = {"section": "ch 2", "scenes": [scene(1, characters=[
        {"name": "Corvin Ashby", "line": "polishes lenses", "new": True,
         "aka": "the pale cartographer", "quote": 'hums for "the pale cartographer"'}])]}
    record, errors, _ = validate(json.dumps(reply), section="ch 2", scene_texts=ch2.scene_texts,
                                 brief=BRIEF, chapter0=False)
    assert errors == [] and record == reply


def test_short_and_long_summaries_only_warn():
    r = good()
    r["scenes"][0]["summary"] = "Marta waits."
    r["scenes"][1]["summary"] = " ".join(["word"] * (SUMMARY_MAX_WORDS + 1))
    record, errors, warnings = check(r)
    assert record == r and errors == []
    assert warnings == ["scene 1: summary has 2 words (expected 8-120)",
                        f"scene 2: summary has {SUMMARY_MAX_WORDS + 1} words (expected 8-120)"]


def test_scene_with_no_characters_only_warns():
    r = good()
    r["scenes"][1]["characters"] = []
    record, _, warnings = check(r)
    assert record == r and warnings == ["scene 2: no characters"]


def test_redefined_brief_term_only_warns_and_shows_both_meanings():
    r = good()
    r["scenes"][0]["terms"] = [{"term": "scrapper", "meaning": "a guard dog"},
                               {"term": "SCRAPPER", "meaning": "A  scavenger\nkid"}]
    record, _, warnings = check(r)
    assert record == r
    assert warnings == ['scene 1: term "scrapper" redefined: brief says "a scavenger kid", reply says "a guard dog"']


def test_warnings_are_not_computed_for_a_rejected_reply():
    r = copy.deepcopy(good())
    r["scenes"][0]["summary"] = "Short."
    r["section"] = "ch 9"
    assert check(r)[2] == []
