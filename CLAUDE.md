# scriptorium

## What this repo is
Python 3.11, standard library only at runtime. Turns books into LLM-usable indexes; v1 is a
spoiler-safe reading companion for novels (Discord thread via OpenClaw). Design of record:
`docs/design.md`.

## Settled names
- **section** — a unit of the story in reading order, labelled as the book prints it (Hive:
  `ch 1`..`ch 13` and the five interludes by name; Children of Dune: `§1`..`§64`, numbered by
  the bot, labelled by epigraph source), with a hidden running order number.
- **chapter** — only what the book itself numbers as one; never renumbered. Not a synonym
  for section.
- **scene** — the stretch between the book's own scene breaks (a Children of Dune § is one
  scene).
- **position** — section + scene the reader has reached; a bare section makes the bot ask
  where in it.
- **cut copy** — the index files up to the position.
- **chapter 0** — the non-story sections marked "always" at split time, by default or
  an owner flip (cast list, a reference glossary); in every cut.
- **brief** — the code-built "story so far" a section worker gets (past only).
- **recap** — what a returning reader gets on "refresh me" / `/recap`; built by the same
  brief-builder at the reader's position. Never call brief or recap "story so far" alone.

## Build / test / run
Run from the repo root; each is green on exit 0.
```
# install:   nothing — uv fetches Python 3.11 and pytest on first run
# build:     uv run --python 3.11 --no-project python -m compileall -q core bot
# test:      uv run --python 3.11 --with pytest pytest
# run local: uv run --python 3.11 --no-project python -S -c "import core"
```

## Main branch (prod target)
- This repo ships from **`main`**. Rebase onto it before cutting a feature branch. The
  workflow itself is in the global `CLAUDE.md`; this line supplies the name.
- Detect rather than guess: `git symbolic-ref refs/remotes/origin/HEAD`.

## Serena
- Verdict: **yes** — "who calls this?" will be asked: the brief-builder feeds workers and
  `/recap`, the merge runs inside the cut, the cut is called by the bot skill and the indexer.
- This line is the project's answer, and it overrides the global default. Markdown under
  `prompts/` is outside the index by nature.

## Gotchas (things that will bite an agent)
- **Nothing newer than Python 3.11.** The host has 3.14; the bot runs the stock OpenClaw
  image's 3.11.2. The build check compiles under 3.11 to catch it.
- **Stdlib only at runtime, no pip.** The run check imports `core` with `-S` (no
  site-packages). pytest is a test-time tool only.
- **Public repo, copyrighted books.** Book content and indexes are never committed:
  `books/`, `index/`, `*.epub` (also refused by the committed `hooks/pre-commit`; enable
  it once per clone with `git config core.hooksPath hooks`).

## The layer chain
- Chain: **core** ──► **prompts** ──► **bot**, one specialist agent per layer.
- Where each lives: `core/` / `prompts/` / `bot/`.
  - `core` — the Python package: split, validate, merge, cut, brief-builder, the
    evaluator's quote-check.
  - `prompts` — the shared worker-instructions file and the Claude Code agent/command files
    (section workers, question writer, judge).
  - `bot` — the OpenClaw side: `bot/SKILL.md`, `index_bot.py`, `new_thread.py`.
- The evaluator has no layer of its own: its code is in `core`, its agents in `prompts`.

## Do NOT
- Never commit anything from `books/` or `index/`, or any `.epub`.
