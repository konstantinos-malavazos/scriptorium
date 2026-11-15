import copy
import io
import json
import re

import pytest

from core.brief import Position
from core.cut import FALLBACK, build_cut, check_answer, cut_dir, main, parse_position, position_line
from core.extract import run
from core.split import load_approved
from tests.index_fixture import StubWorker, fixture_split, write_split

BOOK = "fx"
INSTRUCTIONS = "# Worker instructions\n\nReply with one JSON object."
FILES = ["characters.md", "glossary.md", "position.json", "timeline.md"]
LINE = "Up to ch 3 · scene 2:"


@pytest.fixture
def data(tmp_path):
    write_split(tmp_path, BOOK, fixture_split())
    run(tmp_path, BOOK, INSTRUCTIONS, StubWorker())
    return tmp_path


@pytest.fixture
def approved(data):
    return load_approved(data, BOOK)


def cut_text(data):
    return "".join((cut_dir(data, BOOK) / name).read_text(encoding="utf-8") for name in FILES)


def tags_in_cut(data):
    md = "".join((cut_dir(data, BOOK) / name).read_text(encoding="utf-8") for name in FILES if name.endswith(".md"))
    return sorted(set(re.findall(r"\[[^\[\]]*\]", md)))


def test_position_forms_are_accepted(approved):
    for text in ("ch 3 · scene 2", "ch 3 · 2", "ch 3 scene 2", "ch 3/2", "  ch 3 · 2 "):
        assert parse_position(approved, text) == Position(3, 2), text


def test_section_sign_labels_parse_like_any_other(approved):
    dune = copy.deepcopy(approved)
    for u in dune["sections"]:
        u["label"] = f"§{u['order']}"
    assert parse_position(dune, "§2 · 1") == Position(2, 1)
    assert parse_position(dune, "§3/3") == Position(3, 3)
    with pytest.raises(ValueError, match="which scene"):
        parse_position(dune, "§3")


@pytest.mark.parametrize("text", ["ch 3", "ch 3 · 4", "ch 3 · 0", "Cast · 1", "Cast", "chapter 0 · 1"])
def test_a_position_without_a_real_story_scene_asks_which_scene(approved, text):
    with pytest.raises(ValueError, match=r"which scene\?"):
        parse_position(approved, text)


def test_an_unknown_label_never_lists_the_sections(approved):
    with pytest.raises(ValueError, match="unknown section 'ch 9'") as raised:
        parse_position(approved, "ch 9 · 1")
    message = str(raised.value)
    assert "as the book prints it" in message and 'give it as "ch 1 · scene <k>"' in message
    assert "ch 2" not in message and "ch 3" not in message


def test_position_line():
    assert position_line("ch 6", 3) == "Up to ch 6 · scene 3:"


def test_build_writes_the_four_files_and_the_position(data):
    path = build_cut(data, BOOK, Position(3, 2))
    assert path == cut_dir(data, BOOK) == data / BOOK / "cut"
    assert sorted(p.name for p in path.iterdir()) == FILES
    assert json.loads((path / "position.json").read_text(encoding="utf-8")) == {
        "book": BOOK, "label": "ch 3", "scene": 2, "order": 3, "line": LINE, "chapter0_labels": ["Cast"]}
    timeline = (path / "timeline.md").read_text(encoding="utf-8")
    assert "[ch 3 · 2]" in timeline and "[ch 3 · 3]" not in timeline
    assert "## Tamsin Rook\n- [ch 1 · 1] does quillfeather work, scene 1\n" in (
        path / "characters.md").read_text(encoding="utf-8")
    assert "## lanternwick\n- [Cast] a thing from chapter 0\n" in (path / "glossary.md").read_text(encoding="utf-8")


def test_the_cut_never_names_chapter_0_only_its_printed_labels(data):
    build_cut(data, BOOK, Position(3, 3))
    assert "[chapter 0]" not in cut_text(data)
    assert "## chapter 0" not in cut_text(data)


def test_a_cut_never_opens_a_file_past_the_position(data):
    (data / BOOK / "sections" / "s03.json").write_text("{ corrupt", encoding="utf-8")
    build_cut(data, BOOK, Position(2, 1))
    out = cut_text(data)
    assert "brambleglass" in out and "thornwick" not in out


def test_a_rebuild_at_an_earlier_position_drops_later_content(data):
    build_cut(data, BOOK, Position(3, 3))
    assert "thornwick" in cut_text(data)
    (cut_dir(data, BOOK) / "stray.md").write_text("thornwick", encoding="utf-8")
    build_cut(data, BOOK, Position(1, 1))
    assert sorted(p.name for p in cut_dir(data, BOOK).iterdir()) == FILES
    out = cut_text(data)
    for marker in ("thornwick", "brambleglass", "scene 2"):
        assert marker not in out, marker


def test_a_position_past_the_indexed_sections_is_refused(data):
    (data / BOOK / "sections" / "s02.json").unlink()
    with pytest.raises(ValueError, match="still indexing, at ch 2"):
        build_cut(data, BOOK, Position(3, 1))
    build_cut(data, BOOK, Position(1, 2))


def test_a_missing_chapter_0_is_refused(data):
    (data / BOOK / "sections" / "s00.json").unlink()
    with pytest.raises(ValueError, match="still indexing, at Cast"):
        build_cut(data, BOOK, Position(1, 1))


def test_build_refuses_a_position_the_book_does_not_have(data):
    with pytest.raises(ValueError, match=r"which scene\?"):
        build_cut(data, BOOK, Position(3, 4))
    with pytest.raises(ValueError, match="unknown section"):
        build_cut(data, BOOK, Position(9, 1))


def test_a_bracket_in_a_label_is_refused(tmp_path):
    split = fixture_split()
    split["units"][0]["label"] = "Cast [annotated]"
    write_split(tmp_path, BOOK, split)
    run(tmp_path, BOOK, INSTRUCTIONS, StubWorker())
    with pytest.raises(ValueError, match="bracket"):
        build_cut(tmp_path, BOOK, Position(1, 1))


@pytest.mark.parametrize("answer", [
    f"{LINE}\nTamsin crossed the bridge [ch 1 · 1], and Ilse counted knots [ch 3 · 2].",
    f"{LINE}\nOrrin keeps a ledger [Cast].",
    f"{LINE}\n{FALLBACK}",
    f"{LINE}\n\n{FALLBACK}\n",
])
def test_check_accepts(approved, answer):
    assert check_answer(answer, approved, Position(3, 2)) == []


@pytest.mark.parametrize("answer, error", [
    ("Up to ch 3 · scene 3:\nIlse [ch 3 · 1].", "first line"),
    (f"{LINE}\nIlse [ch 9 · 1].", "unknown citation"),
    (f"{LINE}\nIlse [ch 3 · scene 1].", "unknown citation"),
    (f"{LINE}\nOrrin [chapter 0].", "unknown citation"),
    (f"{LINE}\nTamsin [ch 1 · 3].", "a scene the section doesn't have"),
    (f"{LINE}\nIlse [ch 3 · 3].", "a scene past the reader's"),
    (f"{LINE}\nIlse [ch 3 · 4].", "a scene past the reader's"),
    (f"{LINE}\nTamsin crossed a bridge.", "no citation"),
    (f"{LINE}\n", "no citation"),
    (f"{LINE}\n{FALLBACK} Tamsin crossed a bridge.", "the whole answer"),
    (f"{LINE}\n{FALLBACK} [ch 1 · 1]", "the whole answer"),
])
def test_check_rejects(approved, answer, error):
    errors = check_answer(answer, approved, Position(3, 2))
    assert any(error in e for e in errors), errors


def test_a_section_past_the_reader_never_reveals_its_scene_count(approved):
    errors = check_answer("Up to ch 2 · scene 1:\nIlse [ch 3 · 9].", approved, Position(2, 1))
    assert errors == ["[ch 3 · 9]: a section past the reader's"]


def test_every_tag_the_cut_writes_passes_the_check(data, approved):
    for upto in (Position(1, 1), Position(3, 2), Position(3, 3)):
        build_cut(data, BOOK, upto)
        tags = tags_in_cut(data)
        assert tags
        line = json.loads((cut_dir(data, BOOK) / "position.json").read_text(encoding="utf-8"))["line"]
        assert check_answer(line + "\n" + " ".join(tags), approved, upto) == [], upto


def cli(capsys, monkeypatch, *argv, stdin=""):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_cli_build_prints_the_cut_and_the_line(data, capsys, monkeypatch):
    code, out, err = cli(capsys, monkeypatch, "build", BOOK, "ch 3 · 2", "--data-dir", str(data))
    assert (code, err) == (0, "")
    assert json.loads(out) == {"cut": str(cut_dir(data, BOOK).resolve()), "line": LINE}


@pytest.mark.parametrize("position, message", [("ch 3", "which scene?"), ("ch 9 · 1", "unknown section")])
def test_cli_build_exits_2_on_a_bad_position(data, capsys, monkeypatch, position, message):
    code, out, err = cli(capsys, monkeypatch, "build", BOOK, position, "--data-dir", str(data))
    assert (code, out) == (2, "") and message in err


def test_cli_build_exits_2_without_an_approved_split(tmp_path, capsys, monkeypatch):
    code, _, err = cli(capsys, monkeypatch, "build", BOOK, "ch 1 · 1", "--data-dir", str(tmp_path))
    assert code == 2 and err


def test_cli_build_exits_2_while_indexing(data, capsys, monkeypatch):
    (data / BOOK / "sections" / "s03.json").unlink()
    code, _, err = cli(capsys, monkeypatch, "build", BOOK, "ch 3 · 1", "--data-dir", str(data))
    assert code == 2 and "still indexing, at ch 3" in err


def test_cli_check_exit_codes(data, capsys, monkeypatch):
    assert cli(capsys, monkeypatch, "build", BOOK, "ch 3 · 2", "--data-dir", str(data))[0] == 0
    good = f"{LINE}\nIlse counted knots [ch 3 · 2]."
    assert cli(capsys, monkeypatch, "check", BOOK, "--data-dir", str(data), stdin=good) == (0, "", "")
    code, out, err = cli(capsys, monkeypatch, "check", BOOK, "--data-dir", str(data), stdin=f"{LINE}\nIlse [ch 3 · 3].")
    assert (code, out) == (1, "") and "[ch 3 · 3]: a scene past the reader's" in err


def test_cli_check_exits_2_without_a_cut(data, capsys, monkeypatch):
    code, _, err = cli(capsys, monkeypatch, "check", BOOK, "--data-dir", str(data), stdin=f"{LINE}\n{FALLBACK}")
    assert code == 2 and "build" in err
