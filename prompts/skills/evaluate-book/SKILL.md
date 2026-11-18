---
name: evaluate-book
description: >-
  Evaluate an indexed book's index: a question-writer sub-agent writes 40 accuracy
  questions and 10 spoiler traps from the whole book, code checks them, each one is asked
  through /ask-book at its position, a new judge sub-agent rates each answer, and code
  prints the score (pass at 36/40 right and no leak). Resumes where a run stopped. Use on
  "/evaluate-book <book>" or "evaluate <book>". Needs the book fully indexed (/index-book).
---

# Evaluate a book's index

The book name is `$ARGUMENTS`. Below it is written `<book>`. Run every command from the
repository root.

## Rules

- **Do not read anything under `index/<book>/` or `books/`,** the evaluation's own files
  under `index/<book>/eval/` included. They hold the whole book and its spoilers, and they
  would fill this session's context. You pass paths and text on; the agents and the code
  read them.
- **Write, answer and judge nothing yourself.** The questions come from a
  `question-writer`, every answer from `/ask-book`, every verdict from a `judge`, and the
  score from code. Pass each reply on exactly as received.
- **Every writer and judge attempt is a new dispatch.** Never resume, continue or message
  an earlier one, not even for a retry.
- **Quote arguments for the shell.** Pass `<book>`, `<id>` and a position each inside
  single quotes; a label like `§12` or a `·` must reach the command unchanged. Pass a reply
  on stdin through a quoted heredoc, as shown below.
- **Decode each JSON line before you use it.** Take `step`, `id`, `position`, `question`
  and `message` from the decoded object; never match the raw text.

## Start or resume

1. Run:
   ```
   uv run --python 3.11 --no-project python -m core.evaluate next '<book>'
   ```
   - Exit 0: the questions are already saved and a run is under way. Go to the loop at step
     2, 3 or 4 by its `step`, with this output.
   - Exit 2 with `no questions yet` in its stderr: go to step 2.
   - Exit 2 otherwise: stop, and report its stderr.
2. Write the labelled book:
   ```
   uv run --python 3.11 --no-project python -m core.evaluate book '<book>'
   ```
   - Exit 2: stop, and report its stderr: the split is missing or not approved.
   - Exit 0: it prints one JSON line. Note `book`, the labelled book's path.
3. Dispatch a **new** `question-writer` sub-agent with this message, filled in:
   ```
   book: <book>
   file: <book path>
   ```
   Wait for its reply.
4. Save the questions, passing the reply on stdin exactly as received:
   ```
   uv run --python 3.11 --no-project python -m core.evaluate questions '<book>' <<'END_OF_QUESTIONS'
   <reply>
   END_OF_QUESTIONS
   ```
   - Exit 0: print `<count> questions saved`, and go to the loop.
   - Exit 1, first attempt: dispatch a **new** `question-writer` with the message from step
     3 plus the reply and the errors on stderr:
     ```
     book: <book>
     file: <book path>
     previous:
     <reply>
     errors:
     <stderr lines>
     ```
     Then run this step again on its reply.
   - Exit 1, on the retry: stop. Report the errors. Running `/evaluate-book <book>` again
     starts a fresh pair of attempts.
   - Exit 2: stop, and report its stderr.

## The loop

1. Run:
   ```
   uv run --python 3.11 --no-project python -m core.evaluate next '<book>'
   ```
   Exit 2: stop, and report its stderr.
2. On `"step": "ask"`: run the `ask-book` skill with the arguments
   `<book> <position> <question>`, using the decoded `position` and `question`, and follow
   it to its end. Where it says to show the reader a reply or a message, that text is the
   answer: do not show it, keep it. Then save it exactly as `ask-book` gave it:
   ```
   uv run --python 3.11 --no-project python -m core.evaluate answer '<book>' '<id>' <<'END_OF_ANSWER'
   <answer>
   END_OF_ANSWER
   ```
   Print `<id> answered`, and go to step 1.

   If `answer` exits non-zero: stop, and report the id and its stderr.

   If `ask-book` stopped at any of its exit-2 steps (`core.cut build` or `core.cut check`),
   or ended with no answer to relay, do not save anything: never save an error as an
   answer. Stop, and report the id and its stderr. If it was `core.cut build`, the index
   does not reach that position yet; `/index-book <book>` first.
3. On `"step": "judge"`: dispatch a **new** `judge` sub-agent whose message is the decoded
   `message`, exactly. Wait for its reply, then save it exactly as received:
   ```
   uv run --python 3.11 --no-project python -m core.evaluate judge '<book>' '<id>' <<'END_OF_JUDGEMENT'
   <reply>
   END_OF_JUDGEMENT
   ```
   - Exit 0: print `<id> judged`, and go to step 1.
   - Exit 1, first attempt: dispatch a **new** `judge` with the errors on stderr before the
     same message:
     ```
     errors:
     <stderr lines>

     <message>
     ```
     Then save its reply as above.
   - Exit 1, on the retry: stop. Report the id and the errors. Running
     `/evaluate-book <book>` again resumes at this judge step.
   - Exit 2: stop, and report its stderr.
4. On `"step": "done"`: go to the score.

## The score

Run:
```
uv run --python 3.11 --no-project python -m core.evaluate score '<book>'
```
- Exit 0 (pass) or exit 1 (fail): it prints one line. Print that line, exactly, as the last
  thing you say.
- Exit 2: stop, and report its stderr.

## When you stop early

Report in a few lines: what this run did, where it stopped and why, and the command that
continues it (`/evaluate-book <book>`).
