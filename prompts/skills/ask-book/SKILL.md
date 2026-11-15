---
name: ask-book
description: >-
  Answer a reader's question about an indexed book without spoilers: cut the index at the
  reader's position, have one book-answerer sub-agent answer from the cut alone, check the
  answer's opening line and citations by code, and relay it word for word. Use on
  "/ask-book <book> <position> <question>", e.g. "/ask-book hive ch 6 · scene 3 who is
  Kell?". Needs the book indexed up to the position (/index-book).
---

# Ask about a book

The arguments are `$ARGUMENTS`: the book name, then the reader's position, then the
question. Below they are written `<book>`, `<position>` and `<question>`. Run every command
from the repository root.

## Rules

- **Do not read anything under `index/<book>/`,** the cut included, and nothing under
  `books/`. They hold the whole book and its spoilers. The answerer and the code read them;
  you pass paths and text on.
- **Answer nothing yourself.** Every answer comes from a `book-answerer` and passes `check`
  before the reader sees it. Add nothing to it, and never fill a gap from what you know of
  the book.
- **Every attempt is a new `book-answerer` dispatch.** Never resume, continue or message an
  earlier one.
- **Quote arguments for the shell.** Pass `<book>` and `<position>` each inside single
  quotes; a label like `§12` or a `·` must reach the command unchanged.

## Read the arguments

- `<book>` is the first word.
- `<position>` comes next: a section label as the book prints it, then a scene number, in
  one of the forms `ch 6 · scene 3`, `ch 6 · 3`, `ch 6 scene 3` or `ch 6/3`.
- `<question>` is the rest.

If the arguments hold no question, or you cannot tell where the position ends, ask the
reader for the missing part and stop. A section with no scene still goes to `build`, which
answers with the question to ask.

## Steps

1. Cut the index at the position:
   ```
   uv run --python 3.11 --no-project python -m core.cut build '<book>' '<position>'
   ```
   - Exit 2: relay its stderr to the reader as it is, and stop. It covers a missing scene
     ("which scene?"), an unknown section (asking for the label as the book prints it) and an index not yet
     built that far ("still indexing, at …").
   - Exit 0: it prints one JSON line. Note `cut`, the cut folder's path.
2. Dispatch a **new** `book-answerer` sub-agent with this message, filled in:
   ```
   cut: <cut>
   question: <question>
   ```
   Wait for its reply.
3. Check the reply, passed on stdin exactly as received:
   ```
   uv run --python 3.11 --no-project python -m core.cut check '<book>' <<'END_OF_ANSWER'
   <reply>
   END_OF_ANSWER
   ```
   - Exit 0: relay the reply to the reader word for word, and stop.
   - Exit 1, first attempt: dispatch a **new** `book-answerer` with the message from step
     2 plus the check's stderr:
     ```
     cut: <cut>
     question: <question>
     errors:
     <stderr lines>
     ```
     Then run this step again on its reply.
   - Exit 1, on the retry: do not show either reply, or the errors, which quote it. Tell the
     reader no answer passed the citation check, and that asking again starts a fresh
     attempt.
   - Exit 2: relay its stderr, and stop.
