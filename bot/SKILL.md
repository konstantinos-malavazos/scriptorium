---
name: scriptorium
description: >-
  Index a book from its EPUB with "/index-book <book>" (EPUB attached), answer every message
  in a book's own thread with the indexer's status until the book is ready, and then discuss
  the book there without spoilers, from where the reader says they are.
---

# scriptorium

Every command below runs through exec, exactly as written, from the pinned clone. There are
two scripts and nothing else:

```
cd ~/.openclaw/tools/scriptorium && python3 -m bot.index_bot <command> ... --data-dir ~/.openclaw/scriptorium
cd ~/.openclaw/tools/scriptorium && python3 -m bot.discuss <command> ... --data-dir ~/.openclaw/scriptorium
```

Never run anything else for a book: never read, list or open files under
`~/.openclaw/scriptorium/`, never run `core.*` yourself, and never post into a book thread
yourself. The scripts post their own progress.

Text in single quotes (a thread's name, a section, the reader's words, the key words for
`read`) goes in as it is, except for an apostrophe: write each `'` inside it as `'\''`. For
example, the thread name Ender's Game — Orson Scott Card goes in as
`--thread-name 'Ender'\''s Game — Orson Scott Card'`. Never put words from a thread's name or
a reader's message outside single quotes.

When a rule below says to reply with the last line a command printed, any lines above that
one are for the log, never for the reader.

## /index-book <book>

1. **The book name** is the one word after `/index-book`, for example `hive`. It must be
   letters, digits, `.`, `_` or `-`. If it is missing, ask "Which name should the book
   have? One word, like hive." and stop.
2. **The EPUB** is the file attached to the same message. Use the path OpenClaw saved it
   under (in `~/.openclaw/media/inbound/`).
3. Run:

   ```
   cd ~/.openclaw/tools/scriptorium && python3 -m bot.index_bot start <book> <epub path> --data-dir ~/.openclaw/scriptorium
   ```

   With no attachment, run the same command without `<epub path>`. That resumes a book
   whose EPUB is already saved, and otherwise prints a line asking for the attachment.
4. Reply with the last line the command printed, word for word, and nothing else. It is
   the reply whatever the exit code.

The command returns at once. Indexing goes on in the background and posts into the book's
own thread.

## Any message in a thread

To tell whether a thread is a book thread, run `status` for it; do not look in
`~/.openclaw/scriptorium/` yourself. Before anything else, for **every** message in a
thread, run it with this thread's id, this thread's name and the id of the channel the
thread sits under, as you see them. Leave out a flag only if you cannot see that value.

```
cd ~/.openclaw/tools/scriptorium && python3 -m bot.index_bot status --thread <this thread's id> --thread-name '<this thread's name>' --parent <the parent channel's id> --data-dir ~/.openclaw/scriptorium
```

Look at the exit code and what it printed, together.

- If it exits 2 and printed exactly `not a book thread`, and nothing else, this is not a book
  thread: carry on as usual.
- If it exits 0 and printed exactly `ready`, the book is indexed: follow "Discussing a ready
  book" below.
- If it exits 0 or 3 and the last line it printed is another line (how far indexing has got,
  why it stopped, "I can't tell which book this thread is for.", "I can't read this book's
  index right now." or "Something went wrong on my side. Could you ask that again?"), reply
  with that last line, word for word, and nothing else.
- Anything else (another exit code, exit 2 without that exact line, nothing printed, or an
  error from the shell): reply "I can't read this book's index right now." and nothing else.

In the last two cases, answer nothing: not from the book, not from what you know about it,
not from earlier messages.

## Discussing a ready book

Every step is one `bot.discuss` command. Pass it the same `--thread`, `--thread-name` and
`--parent` you passed to `status`, and `--data-dir ~/.openclaw/scriptorium`, for example:

```
cd ~/.openclaw/tools/scriptorium && python3 -m bot.discuss set 'ch 6 · scene 3' --thread <this thread's id> --thread-name '<this thread's name>' --parent <the parent channel's id> --data-dir ~/.openclaw/scriptorium
```

Exit codes, the same for every command:

- **0**: use the output as the step below says.
- **3**: reply with the last line it printed, word for word, and nothing else. Do not answer the
  question, and do not run the command again in another form. This is how the script asks
  the reader where they are, refuses a section past them, asks for a limit, and tells the
  reader it could not follow a command.
- **1** (`check` only): the answer failed the check; the errors are on stderr.
- Anything else, exit 2 included, or nothing printed: reply "I can't read this book's index
  right now." and answer nothing. `status` already said this is a book thread.

What the reader says, and what you run:

| the reader says | run | then |
|---|---|---|
| where they are: "I'm at ch 6 · scene 3", "ch 6, scene 3" | `set '<section> · scene <k>'` | reply with the printed line |
| only a section: "I'm at ch 6" | `set '<section>'` | it exits 3 asking "Where in ch 6?"; reply with it |
| "just started" / "finished" that section | `set '<section>' --start` / `--end` | reply with the printed line |
| a description of the scene they reached | `find '<their description>'`, with `--within '<section>'` when they named the section, or `--upto '<section or rough %>'` when they gave a limit | reply with the printed lines, word for word, and wait |
| "yes, that one" / picks one of those | `set '<section> · scene <k>'` from the line they picked | reply with the printed line |
| "where am I?" | `where` | reply with the printed line |
| a question that names a section | `place '<section>'` first | `ok`: go on to the question; exit 3: reply with the printed line |
| any question about the book | `read '<key words>' [--section '<section>']`, then `check` | see "Answering" |

Use the section exactly as the reader wrote it; never guess one, never list the book's
sections, and never move the position unless the reader said where they are. A section the
reader merely mentions in a question does not move it. The position only changes on `set`;
if you lose track of a question you asked the reader, ask it again.

### Answering

1. Run `read` with the question's key words, all in one pair of single quotes: names,
   places, things, a few words at most. Add `--section '<section>'` when the question names
   one (after `place` said `ok`). An apostrophe in the key words is written `'\''` here too,
   also when you build the command in code: `read 'the captain'\''s log'`.

   ```
   cd ~/.openclaw/tools/scriptorium && python3 -m bot.discuss read '<key words>' --thread <this thread's id> --thread-name '<this thread's name>' --parent <the parent channel's id> --data-dir ~/.openclaw/scriptorium
   ```

2. Write the answer from the `read` output alone, never from what you know about the book,
   its author, its sequels or adaptations:
   - line 1 is exactly the first line `read` printed, for example `Up to ch 6 · scene 3:`;
   - every fact carries a citation copied from the output, in square brackets, for example
     `[ch 2 · 3]` or a chapter-0 label such as `[Cast]`;
   - answer from whatever the output holds that bears on the question, even when it is only
     part of what was asked, and stop after what you can cite: say nothing about what is
     missing, no sentence that a name, a reason or anything else isn't given or isn't there;
   - only when the output holds nothing on the question, the whole answer after line 1 is
     exactly `That's not in what you've read so far.` Never reword that line (no "isn't
     named", "not yet" or "so far" of your own) and never hint that something comes later;
   - write "the book hasn't said" only as a cited sentence, when a line of the output itself
     shows the thing is unknown.
3. Check it before posting, with the answer on stdin:

   ```
   cd ~/.openclaw/tools/scriptorium && python3 -m bot.discuss check --thread <this thread's id> --thread-name '<this thread's name>' --parent <the parent channel's id> --data-dir ~/.openclaw/scriptorium <<'ANSWER'
   <your answer>
   ANSWER
   ```

4. Exit 0: post the answer exactly as checked. Exit 1: rewrite it once, fixing every error
   it printed, and check again. `check` rejects the fallback's words ("so far", "what
   you've read", "not yet", "isn't named", "not named") anywhere but in the exact fallback.
   If the second try also fails, post nothing of it; reply
   "I couldn't write an answer that passed the spoiler check. Ask again to try afresh."
   Exit 3: reply with the last line it printed.

## Setup

Indexing calls the model `deepseek-index/deepseek-v4-flash`. That provider and model must
exist in `openclaw.json`, with its 200k context cap, and be in
`agents.defaults.modelPolicy.allow`. Without it every `/index-book` stops at its first
section with "openclaw failed".
