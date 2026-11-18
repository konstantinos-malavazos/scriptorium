from pathlib import Path

from core.epub import read_epub
from tests.epub_fixture import write_epub

FILES = [("a.xhtml", "<p>x</p>")]


def test_reads_title_and_author(tmp_path: Path) -> None:
    book = read_epub(write_epub(tmp_path / "b.epub", FILES, title="Hive", authors=["A. Writer"]))
    assert (book.title, book.author) == ("Hive", "A. Writer")


def test_author_is_the_first_creator(tmp_path: Path) -> None:
    book = read_epub(write_epub(tmp_path / "b.epub", FILES, authors=["First One", "Second One"]))
    assert book.author == "First One"


def test_whitespace_is_collapsed(tmp_path: Path) -> None:
    path = write_epub(tmp_path / "b.epub", FILES, title="  The\n  Big   Book ", authors=[" A.\n  Writer "])
    book = read_epub(path)
    assert (book.title, book.author) == ("The Big Book", "A. Writer")


def test_absent_author_and_empty_title_are_empty_strings(tmp_path: Path) -> None:
    book = read_epub(write_epub(tmp_path / "b.epub", FILES, title=""))
    assert (book.title, book.author) == ("", "")
