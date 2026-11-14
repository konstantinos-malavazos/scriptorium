# scriptorium

Turns books into indexes an LLM can use.

The first target is novels: a spoiler-safe reading companion. scriptorium indexes a novel
section by section, then lets you discuss it in its own Discord thread from wherever you
are in the story. It answers only from what you have already reached. That works whether
you are reading the EPUB, listening to the audiobook, or coming back after months away.

> **Status: scaffolded, no features yet.** The v1 design is settled on the
> [map](https://github.com/konstantinos-malavazos/scriptorium/issues/1), one decision per
> closed ticket. The repo holds an empty stub that builds and tests. The work backlog is cut
> next.

## What v1 does

- **Index a novel from its EPUB.** One script splits the book into sections and scenes,
  using its table of contents or divider lines, and the owner confirms the outline before
  indexing starts. Section workers then go through the book in order. Each one gets a
  code-built *brief* of the story so far (past only) and returns one JSON object per
  section: scenes, events, characters, places and terms. A validator rejects bad output,
  allowing one retry.
- **Discuss it from your position.** Each book gets its own Discord thread on an OpenClaw
  bot. Your *position* is a section plus a scene, and it moves only when you say so. A
  bare "I'm at ch 6" makes the bot ask where in ch 6 you are.
- **Spoiler safety is structural.** For every question, the bot re-reads a *cut copy* of
  the index that runs up to your position. The cut is built by re-merging sections up to
  that point, so what you have not reached is never loaded. Aliases are linked only where
  the text itself states the link.
- **Every answer names its section.** There is no outside knowledge by default.
- **`/recap`** (or just "refresh me") confirms your position, then gives about one screen
  of cited prose on the story so far.
- **It measures itself.** An evaluator writes 40 accuracy questions and 10 spoiler traps
  from the raw book, each backed by a verified quote, and a fresh Claude judge marks the
  answers. The Claude Code index must score at least 36/40 with 0 leaks. The bot must land
  within 3 of it, also with 0 leaks. This has to hold on *Hive* (Dan Abnett) and then on
  *Children of Dune* (Frank Herbert). Nobody reviews output by eye; the evaluator is the
  only quality gate.

**After v1:** `/web`, a web lookup with spoilers filtered out. Later: turning technical
books into Claude Code skills.

## Words used here

- **section** is a unit of the story as the book labels it (`ch 1`, an interlude, `§12`).
- **chapter** is only something the book itself numbers as one.
- **scene** is the stretch between the book's own scene breaks.
- **position** is the section and scene you have reached.
- **cut copy** is the index up to your position.
- **brief** is what a section worker gets.
- **recap** is what a returning reader gets.

## Development

Python 3.11, standard library only at runtime, so the scripts run unchanged in the stock
OpenClaw image. [`uv`](https://docs.astral.sh/uv/) fetches Python 3.11 and pytest on
first run. From the repo root:

```
uv run --python 3.11 --no-project python -m compileall -q core bot   # build
uv run --python 3.11 --no-project python -S -c "import core"         # run (no site-packages)
uv run --python 3.11 --with pytest pytest                            # test
```

Layers: `core/` (split, validate, merge, cut, brief-builder, quote-check) → `prompts/`
(worker instructions, evaluator agents) → `bot/` (the OpenClaw skill and scripts).

## Book content stays local

This is a personal tool and the repo is public. Books and their indexes are copyrighted
material. They stay on the owner's machine and are never committed (`books/`, `index/` and
`*.epub` are ignored).

## Docs

- [`docs/design.md`](docs/design.md): the original design notes. Where they disagree with
  a closed ticket on the [map](https://github.com/konstantinos-malavazos/scriptorium/issues/1),
  the ticket wins.
- [`CLAUDE.md`](CLAUDE.md): the facts an agent needs to work in this repo.

## License

[MIT](LICENSE) for the code. Books and indexes are not covered by it and are not part of
this repo.
