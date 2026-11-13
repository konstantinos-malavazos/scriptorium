# scriptorium

Turns books into indexes an LLM can use.

The first target is novels. scriptorium indexes a novel chapter by chapter, so you can ask
*"who is this?"* or *"what happened there?"* while reading or listening, and it only answers
from the chapters you have already reached.

> **Status: design stage.** There is no code yet. The design is being settled on the
> [map](https://github.com/konstantinos-malavazos/scriptorium/issues/1), one decision per
> ticket. Setup and usage will be written here once the stack is chosen.

## What it will do

- **Index a book.** Split an EPUB into chapters. Extract each chapter in order: a summary,
  events, characters with their aliases, places and invented terms. Merge the results into
  character timelines, a glossary and a timeline of the story.
- **Answer without spoilers.** A Discord thread per book only loads the index up to your
  reading position. It cannot leak what it never loaded. Every answer cites the chapter it
  comes from.
- **Look things up on the web, filtered.** A web search runs on request only, and plot
  spoilers past your position are removed before you see the answer.
- **Measure itself.** An evaluator writes questions from the raw book, each backed by an
  exact quote, and scores the index against them, including spoiler traps.

Planned commands:

```
/index-book <epub>                  index a book and open its thread
/ask-book <book> [chN] <question>   one-off question
/web <question>                     spoiler-filtered web lookup, inside a book thread
```

Later: turning technical books into Claude Code skills.

## Book content stays local

This is a personal tool and the repo is public. Books and their indexes are copyrighted
material: they stay on the owner's machine and are never committed (`books/`, `index/` and
`*.epub` are ignored).

## Docs

- [`docs/design.md`](docs/design.md): the original design notes. Open questions are
  tracked on the map, not there.

## License

[MIT](LICENSE) for the code. Books and indexes are not covered by it and are not part of
this repo.
