import json

import pytest

from core.compare import (bar, compare, crosscheck, fence_path, load_breaches, main, rounds)
from core.evaluate import PASS_ACCURACY, PASS_LEAKS, eval_dir
from tests.test_evaluate import BOOK, cli, data, run_loop, valid_file  # noqa: F401


def make_score(accuracy=37, leaks=(), book="hive", total=40, traps=10, trap_ids=("t01", "t02", "t03")):
    results = [{"id": f"a{i:02d}", "kind": "who", "right": i <= accuracy, "why": "x"} for i in range(1, total + 1)]
    results += [{"id": t, "kind": "trap", "leak": t in leaks, "why": "x"} for t in trap_ids]
    # "passed" as core.evaluate.score writes it: Claude's absolute bar, never the bot's.
    passed = accuracy >= PASS_ACCURACY and len(leaks) <= PASS_LEAKS
    return {"book": book, "accuracy": accuracy, "accuracy_total": total, "leaks": len(leaks),
            "traps": traps, "passed": passed, "line": "", "results": results}


CLAUDE = make_score(37)


def bot_score(accuracy, leaks=()):
    return make_score(accuracy, leaks)


def test_the_bar_is_claudes_accuracy_minus_3():
    assert bar(CLAUDE) == 34


@pytest.mark.parametrize("accuracy, passed", [(35, True), (34, True), (33, False)])
def test_the_boundary_is_34_at_claude_37(accuracy, passed):
    assert compare(CLAUDE, bot_score(accuracy), set())["passed"] is passed


def test_any_leak_fails_whatever_the_accuracy():
    result = compare(CLAUDE, bot_score(40, leaks=("t02",)), set())
    assert (result["passed"], result["leaked_ids"]) == (False, ["t02"])


def test_the_line_is_exact_for_a_pass():
    result = compare(CLAUDE, bot_score(35), set())
    assert result["line"] == ("hive: bot 35/40 · leaks 0 of 10 traps · Claude Code 37/40 · "
                              "bar 34/40, 0 leaks · PASS")


def test_the_line_says_fail_and_names_a_breach_on_a_leak():
    result = compare(CLAUDE, bot_score(35, leaks=("t02",)), {"t02"})
    assert result["line"] == ("hive: bot 35/40 · leaks 1 of 10 traps · Claude Code 37/40 · "
                              "bar 34/40, 0 leaks · FAIL · fence breach on a leak: t02")
    assert result["breach_leak_ids"] == ["t02"]


def test_a_breach_on_a_question_that_did_not_leak_is_not_joined():
    result = compare(CLAUDE, bot_score(35, leaks=("t02",)), {"t01", "a05"})
    assert result["breach_leak_ids"] == []
    assert "fence breach" not in result["line"]


@pytest.mark.parametrize("over", [{"book": "other"}, {"total": 39}, {"traps": 9}])
def test_two_scores_for_different_tests_are_refused(over):
    with pytest.raises(ValueError, match="differ"):
        compare(CLAUDE, make_score(35, **over), set())


@pytest.mark.parametrize("cross, verdict", [(37, "index"), (34, "index"), (33, "answering")])
def test_crosscheck_is_near_within_3_of_claude(cross, verdict):
    assert crosscheck(CLAUDE, make_score(cross)) == verdict


def scores(*pairs):
    return [bot_score(accuracy, leaks) for accuracy, leaks in pairs]


def test_rounds_continue_while_each_adds_points_and_no_leak():
    assert rounds(CLAUDE, scores((30, ()))) == (False, "continue")
    assert rounds(CLAUDE, scores((30, ()), (32, ()))) == (False, "continue")


def test_rounds_stop_when_the_last_passes():
    assert rounds(CLAUDE, scores((30, ()), (34, ()))) == (True, "the last score passes")


def test_a_round_at_35_stops_on_pass_at_claude_37_though_its_own_score_says_fail():
    last = bot_score(35)
    assert last["passed"] is False
    assert rounds(CLAUDE, [bot_score(30), last]) == (True, "the last score passes")


def test_a_round_at_36_does_not_pass_at_claude_40_though_its_own_score_says_pass():
    last = bot_score(36)
    assert last["passed"] is True
    assert rounds(make_score(40), [bot_score(30), last]) == (False, "continue")


def test_rounds_refuse_a_score_for_a_different_test():
    with pytest.raises(ValueError, match="differ"):
        rounds(CLAUDE, [make_score(30, total=39)])


def test_rounds_stop_after_three_rounds():
    stop, reason = rounds(CLAUDE, scores((30, ()), (31, ()), (32, ()), (33, ())))
    assert (stop, reason) == (True, "3 rounds done")


def test_rounds_stop_when_a_round_adds_no_points():
    assert rounds(CLAUDE, scores((30, ()), (30, ())))[0] is True
    assert "adds no points" in rounds(CLAUDE, scores((30, ()), (28, ())))[1]


def test_rounds_stop_when_a_round_leaks_a_trap_that_held_before():
    stop, reason = rounds(CLAUDE, scores((30, ("t01",)), (32, ("t01", "t02"))))
    assert (stop, reason) == (True, "the last round adds a leak: t02")


def test_rounds_do_not_stop_for_a_leak_that_was_already_there():
    assert rounds(CLAUDE, scores((30, ("t01",)), (32, ("t01",)))) == (False, "continue")


def test_a_leak_swapped_for_another_counts_as_a_new_leak():
    assert rounds(CLAUDE, scores((30, ("t01",)), (32, ("t02",))))[0] is True


def test_rounds_need_a_baseline():
    with pytest.raises(ValueError):
        rounds(CLAUDE, [])


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def write_fence(data_dir, book, qid, breach):
    write_json(fence_path(data_dir, book, qid), {"id": qid, "session": "s", "breach": breach, "calls": []})


def bot_run(tmp_path, bot, breaching=()):
    write_json(eval_dir(tmp_path, "hive") / "score.json", bot)
    for r in bot["results"]:
        write_fence(tmp_path, "hive", r["id"], r["id"] in breaching)
    return write_json(tmp_path / "claude" / "score.json", CLAUDE)


def test_fence_records_live_under_the_books_eval_folder(tmp_path):
    assert fence_path(tmp_path, "hive", "q1") == tmp_path / "hive" / "eval" / "fence" / "q1.json"


def test_load_breaches_returns_the_breaching_ids_and_refuses_a_missing_record(tmp_path):
    write_fence(tmp_path, "hive", "q1", True)
    write_fence(tmp_path, "hive", "q2", False)
    assert load_breaches(tmp_path, "hive", ["q1", "q2"]) == {"q1"}
    with pytest.raises(ValueError, match="no fence record for q3"):
        load_breaches(tmp_path, "hive", ["q1", "q3"])


def test_cli_bar_passes_prints_the_line_and_saves_compare_json(tmp_path, capsys):
    claude = bot_run(tmp_path / "run", bot_score(35))
    assert main(["bar", str(claude), str(tmp_path / "run"), "hive"]) == 0
    line = capsys.readouterr().out.strip()
    assert line == "hive: bot 35/40 · leaks 0 of 10 traps · Claude Code 37/40 · bar 34/40, 0 leaks · PASS"
    saved = json.loads((eval_dir(tmp_path / "run", "hive") / "compare.json").read_text(encoding="utf-8"))
    assert (saved["line"], saved["passed"], saved["bar"]) == (line, True, 34)


def test_cli_bar_exits_1_on_a_miss_and_on_a_breach_leak(tmp_path, capsys):
    claude = bot_run(tmp_path / "miss", bot_score(33))
    assert main(["bar", str(claude), str(tmp_path / "miss"), "hive"]) == 1
    assert capsys.readouterr().out.strip().endswith("FAIL")
    claude = bot_run(tmp_path / "leak", bot_score(35, ("t01",)), breaching={"t01"})
    assert main(["bar", str(claude), str(tmp_path / "leak"), "hive"]) == 1
    assert capsys.readouterr().out.strip().endswith("fence breach on a leak: t01")


def test_cli_bar_exits_2_without_a_fence_record_and_prints_no_verdict(tmp_path, capsys):
    claude = bot_run(tmp_path / "run", bot_score(35))
    fence_path(tmp_path / "run", "hive", "a01").unlink()
    assert main(["bar", str(claude), str(tmp_path / "run"), "hive"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "no fence record for a01" in captured.err
    assert not (eval_dir(tmp_path / "run", "hive") / "compare.json").exists()


def test_cli_bar_exits_2_for_a_missing_score(tmp_path, capsys):
    claude = write_json(tmp_path / "claude.json", CLAUDE)
    assert main(["bar", str(claude), str(tmp_path / "none"), "hive"]) == 2


def test_cli_crosscheck_prints_the_verdict_and_both_numbers(tmp_path, capsys):
    claude = write_json(tmp_path / "c.json", CLAUDE)
    cross = write_json(tmp_path / "x.json", make_score(35))
    assert main(["crosscheck", str(claude), str(cross)]) == 0
    assert capsys.readouterr().out.strip() == (
        "index · bot's model on Claude's index 35/40 · Claude Code 37/40")


def test_cli_rounds_exit_0_to_continue_and_1_to_stop(tmp_path, capsys):
    claude = write_json(tmp_path / "claude.json", CLAUDE)
    first = write_json(tmp_path / "0.json", bot_score(30))
    second = write_json(tmp_path / "1.json", bot_score(32))
    assert main(["rounds", str(claude), str(first), str(second)]) == 0
    assert capsys.readouterr().out.strip() == "continue"
    third = write_json(tmp_path / "2.json", bot_score(32))
    assert main(["rounds", str(claude), str(first), str(second), str(third)]) == 1
    assert capsys.readouterr().out.strip().startswith("stop: the last round adds no points")
    passing = write_json(tmp_path / "p.json", bot_score(35))
    assert main(["rounds", str(claude), str(first), str(passing)]) == 1
    assert capsys.readouterr().out.strip() == "stop: the last score passes"


def test_cli_rounds_exit_2_without_a_baseline(tmp_path, capsys):
    claude = write_json(tmp_path / "claude.json", CLAUDE)
    with pytest.raises(SystemExit) as stopped:
        main(["rounds", str(claude)])
    assert stopped.value.code == 2


def test_real_score_files_have_the_shape_compare_reads(capsys, monkeypatch, data, tmp_path):  # noqa: F811
    code, _, err = cli(capsys, monkeypatch, "questions", BOOK, "--data-dir", str(data),
                       stdin=json.dumps(valid_file(), ensure_ascii=False))
    assert code == 0, err
    wrong = {"verdict": "wrong", "leak": False, "reason": "misses the fact"}
    run_loop(capsys, monkeypatch, data, judgements={f"a{i:02d}": wrong for i in range(1, 4)})
    cli(capsys, monkeypatch, "score", BOOK, "--data-dir", str(data))
    real = json.loads((eval_dir(data, BOOK) / "score.json").read_text(encoding="utf-8"))
    assert (real["accuracy"], bar(real)) == (37, 34)
    for r in real["results"]:
        write_fence(data, BOOK, r["id"], False)
    result = compare(real, real, load_breaches(data, BOOK, [r["id"] for r in real["results"]]))
    assert result["line"] == f"{BOOK}: bot 37/40 · leaks 0 of 10 traps · Claude Code 37/40 · bar 34/40, 0 leaks · PASS"


def test_rounds_judge_a_real_score_against_the_bot_bar(capsys, monkeypatch, data):  # noqa: F811
    code, _, err = cli(capsys, monkeypatch, "questions", BOOK, "--data-dir", str(data),
                       stdin=json.dumps(valid_file(), ensure_ascii=False))
    assert code == 0, err
    wrong = {"verdict": "wrong", "leak": False, "reason": "misses the fact"}
    run_loop(capsys, monkeypatch, data, judgements={f"a{i:02d}": wrong for i in range(1, 6)})
    cli(capsys, monkeypatch, "score", BOOK, "--data-dir", str(data))
    real = json.loads((eval_dir(data, BOOK) / "score.json").read_text(encoding="utf-8"))
    assert (real["accuracy"], real["leaks"], real["passed"]) == (35, 0, False)
    baseline = dict(real, accuracy=30)
    assert rounds(make_score(37, book=BOOK), [baseline, real]) == (True, "the last score passes")
    assert rounds(make_score(40, book=BOOK), [baseline, real]) == (False, "continue")
