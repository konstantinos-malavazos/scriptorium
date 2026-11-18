import json

import pytest

from bot import new_thread, openclaw
from bot.new_thread import book_name, open_thread, thread_for, thread_name
from tests.epub_fixture import write_epub

BOOK = "hive"
NAME = "Hive — Some Writer"
CREATE = ["message", "thread", "create", "--channel", "discord", "--target", "channel:1537343965538426951",
          "--thread-name", NAME, "--auto-archive-min", "10080", "--json"]
LIST = ["message", "thread", "list", "--channel", "discord", "--guild-id", "1537182496029020232",
        "--channel-id", "1537343965538426951", "--json"]


def listing(*threads: tuple[str, str]) -> str:
    """`message thread list --json` as OpenClaw 2026.10.4 prints it: the list sits at payload.threads.threads."""
    return json.dumps({"action": "thread-list", "payload": {
        "ok": True, "hasMore": False, "returnedCount": len(threads),
        "threads": {"threads": [{"id": tid, "name": name, "parent_id": "1537343965538426951",
                                 "thread_metadata": {"archived": False}} for tid, name in threads],
                    "members": []}}})


class FakeOpenClaw:
    def __init__(self, create: str = '{"threadId": "900"}', listing: str = "[]", fail: bool = False) -> None:
        self.create, self.listing, self.fail = create, listing, fail
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(list(args))
        if args[:3] == ["message", "thread", "create"]:
            if self.fail:
                raise openclaw.OpenClawError("exit 1: missing permissions")
            return self.create
        if args[:3] == ["message", "thread", "list"]:
            return self.listing
        raise AssertionError(f"unexpected call {args}")


@pytest.fixture
def data(tmp_path):
    (tmp_path / BOOK).mkdir()
    write_epub(tmp_path / BOOK / f"{BOOK}.epub", [("a.xhtml", "<p>x</p>")], title="Hive", authors=["Some Writer"])
    return tmp_path


def threads(data):
    return json.loads((data / "threads.json").read_text(encoding="utf-8"))


def fake(monkeypatch, **kwargs) -> FakeOpenClaw:
    stub = FakeOpenClaw(**kwargs)
    monkeypatch.setattr(openclaw, "call", stub)
    return stub


def test_thread_name_falls_back_to_the_book_name_and_drops_a_missing_author():
    assert thread_name("Hive", "Some Writer", "hive") == "Hive — Some Writer"
    assert thread_name("Hive", "", "hive") == "Hive"
    assert thread_name("", "Some Writer", "hive") == "hive — Some Writer"
    assert thread_name("", "", "hive") == "hive"


def test_creates_the_thread_with_the_exact_command_and_records_it(data, monkeypatch):
    stub = fake(monkeypatch)
    assert open_thread(data, BOOK) == "900"
    assert stub.calls == [CREATE]
    assert threads(data) == {"900": BOOK}


def test_without_an_id_in_the_reply_it_lists_threads_and_the_newest_exact_name_wins(data, monkeypatch):
    stub = fake(monkeypatch, create="{}", listing=listing(("5", NAME), ("70", NAME), ("99", NAME + " II")))
    assert open_thread(data, BOOK) == "70"
    assert stub.calls == [CREATE, LIST]
    assert threads(data) == {"70": BOOK}


def test_no_id_anywhere_raises_and_records_nothing(data, monkeypatch):
    fake(monkeypatch, create="created", listing="[]")
    with pytest.raises(openclaw.OpenClawError, match="no thread id"):
        open_thread(data, BOOK)
    assert not (data / "threads.json").exists()


def test_a_failed_create_records_nothing(data, monkeypatch):
    fake(monkeypatch, fail=True)
    with pytest.raises(openclaw.OpenClawError, match="missing permissions"):
        open_thread(data, BOOK)
    assert not (data / "threads.json").exists()


def test_recording_keeps_other_books(data, monkeypatch):
    (data / "threads.json").write_text(json.dumps({"111": "dune"}), encoding="utf-8")
    fake(monkeypatch)
    open_thread(data, BOOK)
    assert threads(data) == {"111": "dune", "900": BOOK}


def test_a_second_call_reuses_the_recorded_thread(data, monkeypatch):
    stub = fake(monkeypatch)
    assert open_thread(data, BOOK) == "900"
    assert open_thread(data, BOOK) == "900"
    assert stub.calls == [CREATE]
    assert thread_for(data, BOOK) == "900"
    assert thread_for(data, "dune") is None


def test_book_name_refuses_bad_names_and_the_threads_file():
    assert book_name("hive") == "hive"
    for bad in ("threads.json", "../x", "", "-x"):
        with pytest.raises(ValueError):
            book_name(bad)


def test_cli_prints_the_thread_id(data, monkeypatch, capsys):
    fake(monkeypatch)
    assert new_thread.main([BOOK, "--data-dir", str(data)]) == 0
    assert capsys.readouterr().out == "900\n"
