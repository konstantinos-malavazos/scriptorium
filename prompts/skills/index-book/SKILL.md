---
name: index-book
description: >-
  Index a book whose split is approved: one section-worker sub-agent per section, in
  reading order, chapter 0 first, each reply checked by code before the next section
  starts. Resumes at the first section with no saved output. Use on "/index-book <book>"
  or "index <book>". Needs an approved split (python -m core.split outline / approve).
---

# Index a book

The book name is `$ARGUMENTS`. Below it is written `<book>`. Run every command from the
repository root.

## Rules

- **One worker at a time, strictly in order.** Never start a worker before the previous
  slot's `submit` has returned.
- **Every attempt is a new `section-worker` dispatch.** Never resume, continue or message
  an earlier worker, not even for a retry: a new dispatch is what gives the retry a fresh
  session.
- **Do not read the prompt file, the reply file, or anything under `index/<book>/`.** They
  hold book text and spoilers, and they would fill this session's context. You pass the
  paths on; the worker and the code read them.
- **Decode each JSON line before you use it.** `next` and `submit` print one JSON line
  each, ASCII-escaped: a label like `§12` arrives as `"§12"`. Take `slot`, `section`,
  `prompt`, `reply`, `outcome` and `errors` from the decoded object; never match the raw
  text. Show the decoded `section` to the user.

## Before the loop

Get the slot ids in reading order, for the progress count:

```
uv run --python 3.11 --no-project python -c "import json, sys; from core.extract import slots; from core.split import load_approved, DEFAULT_DATA_DIR; print(json.dumps([s.id for s in slots(load_approved(DEFAULT_DATA_DIR, sys.argv[1]))]))" <book>
```

If it fails, stop and report its stderr: the split is missing or not approved.

## The loop

1. Run:
   ```
   uv run --python 3.11 --no-project python -m core.extract next <book>
   ```
   - Exit 2: stop, and report its stderr.
   - Exit 0 with `{"done": true}`: every slot has output. Report that `<book>` is fully
     indexed, with the count of slots, and stop.
   - Exit 0 otherwise: note `slot`, `section`, `attempt`, `prompt` and `reply`.
2. Dispatch a **new** `section-worker` sub-agent with this message, the two paths filled
   in:
   ```
   prompt: <prompt path>
   reply: <reply path>
   ```
   Wait for it to finish. It answers `written`. Whatever it answers, go on to step 3:
   `submit` decides.
3. Run:
   ```
   uv run --python 3.11 --no-project python -m core.extract submit <book> <slot>
   ```
   - Exit 0, accepted: print one line, `<section> done (<i>/<n>)`, where `<i>` is the
     slot's position in the slot list (1-based) and `<n>` its length. Go to step 1.
   - Exit 3, rejected with one retry left: print `<section> rejected on attempt
     <attempt>, retrying` and the number of errors. Go to step 1; the next `next` builds
     the retry prompt, errors included.
   - Exit 1, rejected on the retry: stop. Report the section, the decoded `errors`, and
     the two saved rejections,
     `index/<book>/sections/<slot>.rejected-<attempt - 1>.txt` and
     `index/<book>/sections/<slot>.rejected-<attempt>.txt`, each with its
     `.errors.txt` beside it. Running `/index-book <book>` again starts a fresh pair of
     attempts at this slot.
   - Exit 2: stop, and report its stderr.

A `warnings` count above zero does not stop the loop; the warnings are logged beside the
section's output. Mention the count in the progress line.

## When you stop

Report in a few lines: the slots done in this run, where it stopped and why, and the
command that continues it (`/index-book <book>`).
