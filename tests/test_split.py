import copy
import json
from pathlib import Path

import pytest

from core import split
from epub_fixture import divider_book, links_book, mostly_appendix_book, toc_book

BOOKS = Path(__file__).resolve().parent.parent / "books"
HIVE = BOOKS / "HIVE - Dan Abnett.epub"
COD = BOOKS / "Children of Dune - Frank Herbert.epub"


def story(s):
    return [u for u in s["units"] if u["kind"] == "story"]


def by_label(s, label):
    (unit,) = [u for u in s["units"] if u["label"] == label]
    return unit


def roles(unit):
    return [h["role"] for h in unit["heading"] if h["role"] != "blank"]


def test_toc_book_units_defaults_and_why(tmp_path):
    s = split.build_split(toc_book(tmp_path), "toc")
    got = [
        (u["kind"], u["label"], u.get("where"), u.get("why"), u.get("default"), u.get("order"))
        for u in s["units"]
    ]
    assert got == [
        ("non-story", "(no TOC entry)", "before", "cover", "drop", None),
        ("non-story", "Contents", "before", "contents", "drop", None),
        ("non-story", "Legend", "before", "before", "always", None),
        ("non-story", "A Title", "before", "empty", "drop", None),
        ("non-story", "Acknowledgments", "before", "before", "always", None),
        ("story", "The Quiet", None, None, None, 1),
        ("story", "ch 1", None, None, None, 2),
        ("non-story", "Part Two", "inside", "inside", "drop", None),
        ("story", "ch 2", None, None, None, 3),
        ("non-story", "Afterword", "after", "after", "drop", None),
        ("non-story", "About the Author", "after", "after", "drop", None),
    ]
    assert [u["id"] for u in s["units"] if u["kind"] == "non-story"] == [f"x{n}" for n in range(1, 9)]
    assert all(u["decision"] == u["default"] for u in s["units"] if u["kind"] == "non-story")


def test_toc_book_strips_external_link_paragraphs(tmp_path):
    s = split.build_split(toc_book(tmp_path), "toc")
    text = json.dumps(s)
    assert "Example.com" not in text
    assert split.run_checks(s) == []


def test_untitled_file_joins_unit_before_and_standalone_epigraph_is_heading(tmp_path):
    quiet = by_label(split.build_split(toc_book(tmp_path), "toc"), "The Quiet")
    # the verse starts inside the epigraph window; its attribution sits past it
    assert roles(quiet) == ["title"] + ["epigraph"] * 8
    assert quiet["epigraph_source"] == "from the Book of Small Hours"
    assert "source_label" not in quiet
    assert [sc["opening"].split()[0] for sc in quiet["scenes"]] == ["intone"]
    assert len(quiet["scenes"][0]["paragraphs"]) == 3


def test_scene_breaks_on_class_change_after_body_text_and_subtitle(tmp_path):
    ch1 = by_label(split.build_split(toc_book(tmp_path), "toc"), "ch 1")
    assert [(h["role"], h["text"]) for h in ch1["heading"] if h["role"] != "blank"] == [
        ("title", "CHAPTER 1"),
        ("subtitle", "On Small Things"),
    ]
    assert [sc["scene"] for sc in ch1["scenes"]] == [1, 2]
    assert [sc["opening"].split()[0] for sc in ch1["scenes"]] == ["c1sceneone", "c1scenetwo"]
    assert len(ch1["scenes"][0]["opening"].split()) == 12


def test_mid_file_anchor_starts_unit_and_first_paragraph_is_not_a_subtitle(tmp_path):
    s = split.build_split(toc_book(tmp_path), "toc")
    assert by_label(s, "Part Two")["paragraphs"] == ["Part Two"]
    ch2 = by_label(s, "ch 2")
    assert roles(ch2) == ["title"]
    assert ch2["scenes"][0]["opening"].split()[0] == "c2sceneone"
    assert "epigraph_source" not in ch2


@pytest.mark.parametrize("cover_by", ["image", "landmarks"])
def test_contents_by_internal_links_and_cover_without_guide(tmp_path, cover_by):
    s = split.build_split(links_book(tmp_path, cover_by), "links")
    got = [(u["label"], u.get("why"), u.get("default")) for u in s["units"]]
    assert got == [
        ("Cover", "cover", "drop"),
        ("Contents", "contents", "drop"),
        ("Chapter One", None, None),
        ("Chapter Two", None, None),
    ]


def test_divider_sections_cross_files_with_labels_and_front_matter(tmp_path):
    s = split.build_split(divider_book(tmp_path), "div")
    assert [(u["kind"], u["label"]) for u in s["units"]] == [
        ("non-story", "(front matter)"),
        ("story", "§1"),
        ("story", "§2"),
        ("story", "§3"),
        ("story", "§4"),
        ("story", "§5"),
    ]
    front = s["units"][0]
    assert (front["where"], front["why"], front["default"], front["words"]) == ("before", "before", "always", 6)
    assert front["paragraphs"] == ["Some Title Some Author", "Copyright 2001"]
    s1, s2, s3, s4, s5 = story(s)
    assert [u["order"] for u in (s1, s2, s3, s4, s5)] == [1, 2, 3, 4, 5]
    assert (s1["source_label"], roles(s1)) == ("Sayings of the Old Guard", ["epigraph"])
    assert s2["source_label"] == "from a marching song"
    assert [p.split()[0] for p in s2["scenes"][0]["paragraphs"]] == ["s2alpha", "s2beta"]
    assert (s3["source_label"], roles(s3)) == ("Book of Verses", ["epigraph", "epigraph"])
    assert (s4["source_label"], roles(s4)) == (
        "from the Collected Sayings of the Old Guard as told around the winter fires",
        ["epigraph"],
    )
    assert "source_label" not in s5 and "epigraph_source" not in s5
    assert s5["scenes"][0]["paragraphs"][:2] == ["‘I only meant –’", "The gauge on the wall read -5 degrees that morning."]
    assert [len(u["scenes"]) for u in (s1, s2, s3, s4, s5)] == [1, 1, 1, 1, 1]
    assert s1["scenes"][0]["paragraphs"][0].startswith("s1alpha begins -- and")
    assert split.run_checks(s) == []


def clean_split():
    return {
        "book": "b",
        "source": "b.epub",
        "source_words": 14,
        "approved": True,
        "reasons": [],
        "flips": [],
        "units": [
            {"kind": "non-story", "id": "x1", "label": "Legend", "where": "before", "why": "before",
             "words": 2, "default": "always", "decision": "always", "paragraphs": ["two words"]},
            {"kind": "story", "order": 1, "label": "ch 1", "words": 6,
             "heading": [{"role": "title", "text": "Chapter 1"}],
             "scenes": [{"scene": 1, "opening": "one two", "paragraphs": ["one two", "three four"]}]},
            {"kind": "story", "order": 2, "label": "ch 2", "words": 6,
             "heading": [{"role": "epigraph", "text": "Say it -Src"}],
             "scenes": [{"scene": 1, "opening": "five six", "paragraphs": ["five six seven"]}]},
        ],
    }


def _no_story(s):
    s["units"] = [u for u in s["units"] if u["kind"] != "story"]
    s["source_words"] = 2


def _empty_scene(s):
    s["units"][2]["scenes"].append({"scene": 2, "opening": "", "paragraphs": [""]})


def _no_scenes(s):
    s["units"][2]["scenes"] = []
    s["units"][2]["heading"].append({"role": "blank", "text": "five six seven"})


def _dup_label(s):
    s["units"][2]["label"] = "ch 1"


def _bad_order(s):
    s["units"][2]["order"] = 3


def _lost_text(s):
    s["source_words"] = 15


def _other_in_later_heading(s):
    s["units"][2]["heading"].insert(0, {"role": "other", "text": "x"})
    s["source_words"] += 1


def _minority(s):
    s["units"][0]["paragraphs"] = ["w " * 20]
    s["source_words"] = 32


def _bad_decision(s):
    s["units"][0]["decision"] = "maybe"


def _dup_id(s):
    s["units"].append(dict(s["units"][0]))
    s["source_words"] += 2


@pytest.mark.parametrize(
    "defect, check",
    [
        (_no_story, "story-found"),
        (_empty_scene, "no-empty-scene"),
        (_dup_label, "labels-unique"),
        (_bad_order, "order-contiguous"),
        (_lost_text, "text-conserved"),
        (_other_in_later_heading, "no-text-before-epigraph"),
        (_minority, "story-majority"),
        (_bad_decision, "decisions-valid"),
        (_dup_id, "decisions-valid"),
    ],
)
def test_each_check_fires_on_exactly_its_defect(defect, check):
    s = clean_split()
    defect(s)
    reasons = split.run_checks(s)
    assert [r.split(":")[0] for r in reasons] == [check]
    assert reasons[0].startswith(f"{check}: ") and len(reasons[0]) > len(check) + 2


def test_section_with_no_scenes_fails_no_empty_scene():
    s = clean_split()
    _no_scenes(s)
    assert "no-empty-scene" in [r.split(":")[0] for r in split.run_checks(s)]


def test_clean_split_passes_and_first_section_may_hold_other():
    s = clean_split()
    assert split.run_checks(s) == []
    s["units"][1]["heading"].insert(0, {"role": "other", "text": "x"})
    s["source_words"] += 1
    assert split.run_checks(s) == []


def test_check_names_are_the_eight_general_checks():
    assert split.CHECKS == (
        "story-found", "no-empty-scene", "labels-unique", "order-contiguous",
        "text-conserved", "no-text-before-epigraph", "story-majority", "decisions-valid",
    )


@pytest.mark.skipif(not HIVE.exists(), reason="HIVE EPUB not present")
def test_real_hive():
    s = split.build_split(HIVE, "hive")
    st = story(s)
    assert (len(st), sum(len(u["scenes"]) for u in st)) == (18, 177)
    assert split.run_checks(s) == []
    first = s["units"].index(st[0])
    last = s["units"].index(st[-1])
    inside = [u for u in s["units"][first:last] if u["kind"] == "non-story"]
    assert [(u["label"], u["default"]) for u in inside] == [(n, "drop") for n in ("I", "II", "III", "IV")]
    before = s["units"][:first]
    assert {u["label"] for u in before if u["default"] == "always"} == {"Warhammer 40,000", "Acknowledgment", "Quote"}
    assert all(u["kind"] == "non-story" for u in before)
    assert all(u["default"] == "drop" for u in s["units"][last + 1:])
    assert "ch 1" in [u["label"] for u in st] and "ch 13" in [u["label"] for u in st]
    assert sum(1 for u in st if u.get("epigraph_source")) == 5  # the five interludes
    assert not any("OceanofPDF" in sc["opening"] for u in st for sc in u["scenes"])


@pytest.mark.skipif(not COD.exists(), reason="Children of Dune EPUB not present")
def test_real_children_of_dune():
    s = split.build_split(COD, "cod")
    st = story(s)
    assert (len(st), sum(len(u["scenes"]) for u in st)) == (64, 64)
    assert [u["label"] for u in st] == [f"§{k}" for k in range(1, 65)]
    assert [u["label"] for u in st if "source_label" not in u] == []
    assert split.run_checks(s) == []
    assert by_label(s, "§3")["source_label"] == "from a Fremen song"
    fronts = [u for u in s["units"] if u["label"] == "(front matter)"]
    assert len(fronts) == 1
    assert s["units"].index(fronts[0]) == s["units"].index(st[0]) - 1
    assert (fronts[0]["default"], fronts[0]["words"]) == ("always", 7)
    assert not any(h["role"] == "other" for u in st for h in u["heading"])
    assert not any("OceanofPDF" in sc["opening"] for u in st for sc in u["scenes"])


def read(data_dir, book):
    return json.loads((data_dir / book / "split.json").read_text(encoding="utf-8"))


def test_outline_writes_approved_split_and_exits_0(tmp_path, capsys):
    data = tmp_path / "data"
    assert split.main(["outline", str(toc_book(tmp_path)), "toc", "--data-dir", str(data)]) == 0
    s = read(data, "toc")
    assert (s["approved"], s["reasons"], s["flips"]) == (True, [], [])
    out = capsys.readouterr().out
    assert "approved" in out and "flips reset" not in out
    assert "x3 [always] before" in out


def test_outline_on_failing_split_writes_unapproved_and_exits_1(tmp_path, capsys):
    data = tmp_path / "data"
    assert split.main(["outline", str(divider_book(tmp_path, adjacent=True)), "d", "--data-dir", str(data)]) == 1
    s = read(data, "d")
    assert s["approved"] is False
    assert [r.split(":")[0] for r in s["reasons"]] == ["no-empty-scene"]
    assert "no-empty-scene" in capsys.readouterr().out


def test_text_before_later_epigraph_is_not_approved(tmp_path):
    data = tmp_path / "data"
    epub = divider_book(tmp_path, text_before_later_epigraph=True)
    assert split.main(["outline", str(epub), "d", "--data-dir", str(data)]) == 1
    assert [r.split(":")[0] for r in read(data, "d")["reasons"]] == ["no-text-before-epigraph"]


def test_mostly_appendix_is_not_approved(tmp_path):
    data = tmp_path / "data"
    assert split.main(["outline", str(mostly_appendix_book(tmp_path)), "a", "--data-dir", str(data)]) == 1
    assert [r.split(":")[0] for r in read(data, "a")["reasons"]] == ["story-majority"]


def test_approve_flip_toggles_named_units_and_records_flips(tmp_path):
    data = tmp_path / "data"
    split.main(["outline", str(toc_book(tmp_path)), "toc", "--data-dir", str(data)])
    before = read(data, "toc")
    assert split.main(["approve", "toc", "--flip", "x3", "x4", "--data-dir", str(data)]) == 0
    after = read(data, "toc")
    changed = {
        a["id"]: (b["decision"], a["decision"])
        for b, a in zip(before["units"], after["units"])
        if a["kind"] == "non-story" and a["decision"] != b["decision"]
    }
    assert changed == {"x3": ("always", "drop"), "x4": ("drop", "always")}
    assert (after["flips"], after["approved"], after["reasons"]) == (["x3", "x4"], True, [])


def test_approve_cannot_approve_a_failing_split(tmp_path):
    data = tmp_path / "data"
    split.main(["outline", str(divider_book(tmp_path, adjacent=True)), "d", "--data-dir", str(data)])
    assert split.main(["approve", "d", "--flip", "x1", "--data-dir", str(data)]) == 1
    s = read(data, "d")
    assert (s["approved"], s["flips"]) == (False, ["x1"])


def test_approve_unknown_id_exits_2_and_writes_nothing(tmp_path):
    data = tmp_path / "data"
    split.main(["outline", str(toc_book(tmp_path)), "toc", "--data-dir", str(data)])
    path = data / "toc" / "split.json"
    raw = path.read_bytes()
    assert split.main(["approve", "toc", "--flip", "x3", "x99", "--data-dir", str(data)]) == 2
    assert path.read_bytes() == raw


def test_rerunning_outline_resets_flips_and_names_them(tmp_path, capsys):
    data = tmp_path / "data"
    epub = str(toc_book(tmp_path))
    split.main(["outline", epub, "toc", "--data-dir", str(data)])
    split.main(["approve", "toc", "--flip", "x4", "--data-dir", str(data)])
    split.main(["approve", "toc", "--flip", "x3", "x4", "--data-dir", str(data)])
    capsys.readouterr()
    assert split.main(["outline", epub, "toc", "--data-dir", str(data)]) == 0
    assert "flips reset: x4 x3" in capsys.readouterr().out
    s = read(data, "toc")
    assert s["flips"] == []
    assert all(u["decision"] == u["default"] for u in s["units"] if u["kind"] == "non-story")


def test_load_approved_refuses_missing_unapproved_and_hand_edited(tmp_path):
    data = tmp_path / "data"
    with pytest.raises(FileNotFoundError):
        split.load_approved(data, "nope")
    split.main(["outline", str(divider_book(tmp_path, adjacent=True)), "d", "--data-dir", str(data)])
    with pytest.raises(split.SplitNotApproved):
        split.load_approved(data, "d")
    path = data / "d" / "split.json"
    s = json.loads(path.read_text(encoding="utf-8"))
    s["approved"], s["reasons"] = True, []
    path.write_text(json.dumps(s), encoding="utf-8")
    with pytest.raises(split.SplitNotApproved):
        split.load_approved(data, "d")


def test_load_approved_hands_on_story_and_chapter0_never_drop(tmp_path):
    data = tmp_path / "data"
    split.main(["outline", str(toc_book(tmp_path)), "toc", "--data-dir", str(data)])
    got = split.load_approved(data, "toc")
    assert got["book"] == "toc"
    assert [(u["order"], u["label"]) for u in got["sections"]] == [(1, "The Quiet"), (2, "ch 1"), (3, "ch 2")]
    assert [u["label"] for u in got["chapter0"]] == ["Legend", "Acknowledgments"]
    split.main(["approve", "toc", "--flip", "x4", "x3", "--data-dir", str(data)])
    assert [u["label"] for u in split.load_approved(data, "toc")["chapter0"]] == ["A Title", "Acknowledgments"]


def test_hand_built_split_is_not_mutated_by_checks():
    s = clean_split()
    snapshot = copy.deepcopy(s)
    split.run_checks(s)
    assert s == snapshot
