import json

import pytest

from core.brief import Position
from core.cut import build_cut, cut_dir, parse_position
from core.extract import run
from core.reader import (READER_FILE, Match, bare_section, find_scenes, first_sentence, fresh_cut, limit_order,
                         move_to, relation, saved_position, section_edge)
from core.split import load_approved
from tests.index_fixture import StubWorker, fixture_split, write_split

BOOK = "fx"
INSTRUCTIONS = "# Worker instructions\n\nReply with one JSON object."


@pytest.fixture
def data(tmp_path):
    write_split(tmp_path, BOOK, fixture_split())
    run(tmp_path, BOOK, INSTRUCTIONS, StubWorker())
    return tmp_path


@pytest.fixture
def approved(data):
    return load_approved(data, BOOK)


def cut_position(data):
    saved = json.loads((cut_dir(data, BOOK) / "position.json").read_text(encoding="utf-8"))
    return Position(saved["order"], saved["scene"])


def cut_text(data):
    return "".join(p.read_text(encoding="utf-8") for p in sorted(cut_dir(data, BOOK).iterdir()))


# --- saved position and the cut ---

def test_move_to_rebuilds_the_cut_then_saves_the_position(data):
    assert saved_position(data, BOOK) is None
    assert move_to(data, BOOK, Position(3, 2)) == cut_dir(data, BOOK)
    assert json.loads((data / BOOK / READER_FILE).read_text(encoding="utf-8")) == {
        "label": "ch 3", "scene": 2, "order": 3}
    assert saved_position(data, BOOK) == Position(3, 2) == cut_position(data)


def test_moving_back_shrinks_the_cut(data):
    move_to(data, BOOK, Position(3, 3))
    assert "thornwick" in cut_text(data)
    move_to(data, BOOK, Position(1, 1))
    assert saved_position(data, BOOK) == Position(1, 1)
    assert "thornwick" not in cut_text(data) and "brambleglass" not in cut_text(data)


@pytest.mark.parametrize("upto, message", [(Position(3, 4), r"which scene\?"), (Position(9, 1), "unknown section")])
def test_a_refused_position_saves_nothing(data, upto, message):
    with pytest.raises(ValueError, match=message):
        move_to(data, BOOK, upto)
    assert not (data / BOOK / READER_FILE).exists()


def test_a_position_not_indexed_yet_keeps_the_old_one(data):
    move_to(data, BOOK, Position(1, 2))
    (data / BOOK / "sections" / "s03.json").unlink()
    with pytest.raises(ValueError, match="still indexing, at ch 3"):
        move_to(data, BOOK, Position(3, 1))
    assert saved_position(data, BOOK) == Position(1, 2) == cut_position(data)


def test_a_crash_between_the_two_writes_heals_on_the_next_read(data, monkeypatch):
    move_to(data, BOOK, Position(1, 1))

    def crash(*_):
        raise OSError("disk full")

    monkeypatch.setattr("core.reader._write_text", crash)
    with pytest.raises(OSError):
        move_to(data, BOOK, Position(3, 3))
    assert cut_position(data) == Position(3, 3) and saved_position(data, BOOK) == Position(1, 1)
    assert fresh_cut(data, BOOK) == Position(1, 1)
    assert cut_position(data) == Position(1, 1) and "thornwick" not in cut_text(data)


def test_a_missing_cut_is_rebuilt_at_the_saved_position(data):
    move_to(data, BOOK, Position(2, 1))
    build_cut(data, BOOK, Position(3, 1))
    (cut_dir(data, BOOK) / "position.json").unlink()
    assert fresh_cut(data, BOOK) == Position(2, 1) == cut_position(data)


def test_fresh_cut_builds_nothing_before_a_position_is_set(data):
    assert fresh_cut(data, BOOK) is None
    assert not cut_dir(data, BOOK).exists()


# --- bare sections, edges and relations ---

@pytest.mark.parametrize("text, label", [
    ("ch 3", "ch 3"), ("  ch 3 ", "ch 3"), ("ch 3 · 2", None), ("Cast", None), ("ch 9", None), ("ch", None)])
def test_bare_section(approved, text, label):
    assert bare_section(approved, text) == label


def test_section_edges(approved):
    assert section_edge(approved, "ch 3", "start") == Position(3, 1)
    assert section_edge(approved, "ch 3", "end") == Position(3, 3)
    assert section_edge(approved, " ch 2 ", "end") == Position(2, 1)


@pytest.mark.parametrize("label", ["ch 9", "Cast"])
def test_an_edge_of_a_non_story_label_is_refused_as_parse_position_refuses(approved, label):
    with pytest.raises(ValueError) as expected:
        parse_position(approved, f"{label} · 1")
    with pytest.raises(ValueError) as raised:
        section_edge(approved, label, "start")
    assert str(raised.value) == str(expected.value)
    assert "ch 2" not in str(raised.value) and "ch 3" not in str(raised.value)


@pytest.mark.parametrize("label, where", [("ch 1", "behind"), ("ch 2", "here"), ("ch 3", "past"), ("Cast", "behind")])
def test_relation_by_running_order(approved, label, where):
    assert relation(approved, Position(2, 1), label) == where


def test_relation_refuses_an_unknown_label_without_listing_sections(approved):
    with pytest.raises(ValueError, match="unknown section 'ch 9'") as raised:
        relation(approved, Position(2, 1), "ch 9")
    assert "ch 3" not in str(raised.value)


# --- search limits, first sentences and the scene search ---

def test_limit_order_takes_a_label(approved):
    assert limit_order(approved, "ch 2") == 2
    assert limit_order(approved, " ch 3 ") == 3


def test_limit_order_maps_a_percentage_by_story_words(approved):
    words = [u["words"] for u in approved["sections"]]
    total = sum(words)
    first_share = 100 * words[0] / total
    two_shares = 100 * (words[0] + words[1]) / total
    assert limit_order(approved, "0%") == 1
    assert limit_order(approved, f"{int(first_share)}%") == 1
    assert limit_order(approved, f"about {int(first_share) + 1}%") == 2
    assert limit_order(approved, f"Around {int(two_shares) + 1} %") == 3
    assert limit_order(approved, "100%") == 3


@pytest.mark.parametrize("marker", ["part two", "150%", "", "40", "ch 9", "Cast"])
def test_limit_order_refuses_anything_else(approved, marker):
    with pytest.raises(ValueError, match="give a section as the book prints it, or a rough percentage"):
        limit_order(approved, marker)


@pytest.mark.parametrize("paragraphs, sentence", [
    (["Tamsin crossed the bridge. Then she slept."], "Tamsin crossed the bridge."),
    (["“Go now!” she said. He went."], "“Go now!”"),
    (["He said “Stop.” Then he left."], "He said “Stop.”"),
    (["It was 3.5 miles away. Far."], "It was 3.5 miles away."),
    (["Ilse, the salt\nwidow, sold rope", "Second paragraph."], "Ilse, the salt widow, sold rope"),
    ([], ""),
])
def test_first_sentence(paragraphs, sentence):
    assert first_sentence(paragraphs) == sentence


def test_first_sentence_is_capped_at_40_words():
    out = first_sentence([" ".join(f"w{i}" for i in range(60)) + "."])
    assert out == " ".join(f"w{i}" for i in range(40)) + "…"


def test_find_scenes_returns_at_most_three_with_ties_to_the_earlier_scene(data):
    assert len(find_scenes(data, BOOK, "before nightfall", 1, 3, n=9)) == 6
    assert find_scenes(data, BOOK, "before nightfall", 1, 3) == [
        Match("ch 1", 1, "Tamsin Rook crossed the quillfeather bridge at dusk."),
        Match("ch 1", 2, "Tamsin Rook slept under the quillfeather awning."),
        Match("ch 2", 1, "Corvin Ashby polished brambleglass lenses all night."),
    ]


def test_find_scenes_ranks_by_shared_words(data):
    assert find_scenes(data, BOOK, "Tamsin, Ilse, thornwick at nightfall", 1, 3, n=1) == [
        Match("ch 3", 1, "Ilse Marrow, whom the dockers called “the salt widow”, sold thornwick rope.")]


def test_find_scenes_keeps_to_its_range_and_drops_no_match(data):
    assert find_scenes(data, BOOK, "thornwick", 1, 2) == []
    assert [m.label for m in find_scenes(data, BOOK, "nightfall", 2, 3, n=9)] == ["ch 2", "ch 3", "ch 3", "ch 3"]
    assert find_scenes(data, BOOK, "zzz", 1, 3) == []


def test_find_scenes_never_opens_a_file_past_its_limit(data):
    (data / BOOK / "sections" / "s03.json").write_text("{ corrupt", encoding="utf-8")
    assert find_scenes(data, BOOK, "Corvin brambleglass", 1, 2) == [
        Match("ch 2", 1, "Corvin Ashby polished brambleglass lenses all night.")]


def test_find_scenes_returns_no_summary(data):
    for match in find_scenes(data, BOOK, "nightfall", 1, 3, n=9):
        assert "nightfall" not in match.sentence and "does the" not in match.sentence


def test_find_scenes_refuses_a_bad_range(data):
    with pytest.raises(ValueError, match="bad search range"):
        find_scenes(data, BOOK, "Rook", 2, 1)
