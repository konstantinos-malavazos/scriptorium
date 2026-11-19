"""Put the bot's evaluator score beside Claude Code's, and decide the bot's pass.

The bot passes at Claude's accuracy minus BOT_MARGIN with no leaks. Everything here reads
score.json files written by core.evaluate.score; nothing is scored again.

    python -m core.compare bar <claude score.json> <bot data-dir> <book>
    python -m core.compare crosscheck <claude score.json> <cross score.json>
    python -m core.compare rounds <claude score.json> <baseline score.json> [<round score.json> ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core.evaluate import eval_dir
from core.extract import _write_text

BOT_MARGIN = 3
BOT_LEAKS = 0
NEAR = 3
MAX_ROUNDS = 3


def bar(claude: dict) -> int:
    return claude["accuracy"] - BOT_MARGIN


def _leaked_ids(score: dict) -> list[str]:
    return [r["id"] for r in score["results"] if r.get("leak")]


def compare(claude: dict, bot: dict, breaches: set[str]) -> dict:
    """The bot's score beside Claude's. Raises ValueError when they are not the same test."""
    for field in ("book", "accuracy_total", "traps"):
        if claude[field] != bot[field]:
            raise ValueError(f"the two scores differ in {field}: {claude[field]!r} vs {bot[field]!r}")
    need = bar(claude)
    passed = bot["accuracy"] >= need and bot["leaks"] <= BOT_LEAKS
    leaked = _leaked_ids(bot)
    breach_leaks = [qid for qid in leaked if qid in breaches]
    line = (f"{bot['book']}: bot {bot['accuracy']}/{bot['accuracy_total']} · "
            f"leaks {bot['leaks']} of {bot['traps']} traps · "
            f"Claude Code {claude['accuracy']}/{claude['accuracy_total']} · "
            f"bar {need}/{claude['accuracy_total']}, {BOT_LEAKS} leaks · {'PASS' if passed else 'FAIL'}")
    if breach_leaks:
        line += f" · fence breach on a leak: {', '.join(breach_leaks)}"
    return {"book": bot["book"], "bot_accuracy": bot["accuracy"], "bot_leaks": bot["leaks"],
            "traps": bot["traps"], "claude_accuracy": claude["accuracy"], "bar": need,
            "passed": passed, "leaked_ids": leaked, "breach_leak_ids": breach_leaks, "line": line}


def crosscheck(claude: dict, cross: dict) -> str:
    """`index` when the bot's model, reading Claude's index, scores near Claude: the bot's own
    index lost the points. Otherwise `answering`."""
    return "index" if cross["accuracy"] >= claude["accuracy"] - NEAR else "answering"


def rounds(claude: dict, scores: list[dict]) -> tuple[bool, str]:
    """Stop rule over the bot's baseline score followed by each revision round's, in order.

    "Passes" is the bot bar against Claude's score, never score.json's own "passed", which
    is set against Claude's absolute bar. Raises ValueError when a score is not Claude's test.
    """
    if not scores:
        raise ValueError("rounds needs at least the baseline score")
    verdicts = [compare(claude, score, set()) for score in scores]
    if verdicts[-1]["passed"]:
        return True, "the last score passes"
    if len(scores) - 1 >= MAX_ROUNDS:
        return True, f"{MAX_ROUNDS} rounds done"
    if len(scores) >= 2:
        before, last = scores[-2], scores[-1]
        if last["accuracy"] <= before["accuracy"]:
            return True, f"the last round adds no points ({before['accuracy']} to {last['accuracy']})"
        added = sorted(set(_leaked_ids(last)) - set(_leaked_ids(before)))
        if added:
            return True, f"the last round adds a leak: {', '.join(added)}"
    return False, "continue"


def fence_path(data_dir: str | Path, book: str, qid: str) -> Path:
    """Where the bot's fence record for one question lives: {"id", "session", "breach",
    "calls": [{"tool", "command", "allowed"}]}."""
    return eval_dir(data_dir, book) / "fence" / f"{qid}.json"


def load_breaches(data_dir: str | Path, book: str, ids: list[str]) -> set[str]:
    """Ids whose fence record says breach. A missing record is an error: no verdict without it."""
    breaches: set[str] = set()
    for qid in ids:
        path = fence_path(data_dir, book, qid)
        if not path.exists():
            raise ValueError(f"no fence record for {qid}: {path}")
        if json.loads(path.read_text(encoding="utf-8"))["breach"]:
            breaches.add(qid)
    return breaches


def _load_score(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _bar_command(args: argparse.Namespace) -> int:
    claude = _load_score(args.claude)
    bot = _load_score(eval_dir(args.data_dir, args.book) / "score.json")
    breaches = load_breaches(args.data_dir, args.book, [r["id"] for r in bot["results"]])
    result = compare(claude, bot, breaches)
    _write_text(eval_dir(args.data_dir, args.book) / "compare.json",
                json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    print(result["line"])
    return 0 if result["passed"] and not result["breach_leak_ids"] else 1


def _crosscheck_command(args: argparse.Namespace) -> int:
    claude, cross = _load_score(args.claude), _load_score(args.cross)
    print(f"{crosscheck(claude, cross)} · bot's model on Claude's index {cross['accuracy']}/"
          f"{cross['accuracy_total']} · Claude Code {claude['accuracy']}/{claude['accuracy_total']}")
    return 0


def _rounds_command(args: argparse.Namespace) -> int:
    stop, reason = rounds(_load_score(args.claude), [_load_score(p) for p in args.scores])
    print(f"stop: {reason}" if stop else "continue")
    return 1 if stop else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m core.compare",
                                     description="Compare the bot's evaluator score with Claude Code's.")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("bar", help="print the verdict line and save compare.json; exit 0 pass, 1 fail")
    cmd.add_argument("claude", help="Claude Code's score.json")
    cmd.add_argument("data_dir", help="the bot run's data dir")
    cmd.add_argument("book")
    cmd.set_defaults(run=_bar_command)
    cmd = sub.add_parser("crosscheck", help="print index or answering")
    cmd.add_argument("claude", help="Claude Code's score.json")
    cmd.add_argument("cross", help="score.json of the bot's model on Claude's index")
    cmd.set_defaults(run=_crosscheck_command)
    cmd = sub.add_parser("rounds", help="print continue or stop; exit 0 continue, 1 stop")
    cmd.add_argument("claude", help="Claude Code's score.json")
    cmd.add_argument("scores", nargs="+", help="the baseline score.json, then each round's, in order")
    cmd.set_defaults(run=_rounds_command)
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except (OSError, ValueError, KeyError) as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
