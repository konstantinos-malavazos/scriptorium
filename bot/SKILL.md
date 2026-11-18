---
name: scriptorium
description: >-
  Index a book from its EPUB with "/index-book <book>" (EPUB attached), and answer every
  message in a book's own thread with the indexer's status until the book is ready.
---

# scriptorium

Every command below runs through exec, exactly as written, from the pinned clone:

```
cd ~/.openclaw/tools/scriptorium && python3 -m bot.index_bot <command> --data-dir ~/.openclaw/scriptorium
```

Never run anything else for a book: never read, list or open files under
`~/.openclaw/scriptorium/`, never run `core.*` yourself, and never post into a book thread
yourself. The scripts post their own progress.

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
4. Reply with the one line the command printed, word for word, and nothing else. It is
   the same line whatever the exit code.

The command returns at once. Indexing goes on in the background and posts into the book's
own thread.

## Any message in a book thread

To tell whether a thread is a book thread, run `status` for it; do not look in
`~/.openclaw/scriptorium/` yourself. Before anything else, for **every** message in a
thread, run:

```
cd ~/.openclaw/tools/scriptorium && python3 -m bot.index_bot status --thread <this thread's id> --data-dir ~/.openclaw/scriptorium
```

Look at the exit code first.

- If it exits 2, this is not a book thread: ignore what it printed and carry on as usual.
- If it prints exactly `ready`, the book is indexed. Asking about it is not set up yet:
  reply "The book is indexed; asking about it is coming soon."
- If it prints anything else, reply with that line, word for word, and nothing else. Do
  not answer the message.

## Setup

Indexing calls the model `deepseek-index/deepseek-v4-flash`. That provider and model must
exist in `openclaw.json`, with its 200k context cap, and be in
`agents.defaults.modelPolicy.allow`. Without it every `/index-book` stops at its first
section with "openclaw failed".
