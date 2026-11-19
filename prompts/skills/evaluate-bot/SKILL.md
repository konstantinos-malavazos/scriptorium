---
name: evaluate-bot
description: >-
  Score the reading bot's own answers on the questions already written for a book, beside
  Claude Code's score: each question is asked of the bot through its real agent, a new
  judge sub-agent rates each answer, and code prints the verdict against Claude's score
  (pass at Claude's accuracy minus 3 and no leak). Resumes where a run stopped. Use on
  "/evaluate-bot <book> <run> [label]" or "evaluate the bot on <book>". Needs the book
  evaluated by /evaluate-book first and indexed in the bot's data.
---

# Score the bot beside Claude Code

The arguments are `$ARGUMENTS`: `<book> <run> [label]`.

- `<run>` is `bot` (the bot's own index) or `crosscheck` (Claude's index, answered by the
  bot's model).
- `[label]` names this attempt: `baseline` (the default), then `r1`, `r2`, `r3` for
  revision rounds.

Any other `<run>` or `[label]`: stop, and say which values are allowed.

Run every command from the repository root. Each attempt has a run folder of its own,
`~/.openclaw/scriptorium-eval/<run>-<label>` (for example `bot-baseline`, `bot-r1`,
`crosscheck-baseline`), used once and never cleared. Write it with the `~`, never expanded.

## Rules

- **Do not read anything under `index/<book>/`, the run folder or `books/`.** They hold
  the whole book and its spoilers. You pass paths and text on; the agents and the code
  read them.
- **Answer and judge nothing yourself.** Every answer comes from `bot.eval_bot ask`, every
  verdict from a `judge`, the score from code. Never use `ask-book`:
  it answers from Claude's index, not the bot's.
- **Every judge attempt is a new dispatch.** Never resume or message an earlier one.
- **Quote arguments for the shell.** Pass `<book>`, `<id>` inside single quotes. Pass a
  reply on stdin through a quoted heredoc, as shown below.
- **Decode each JSON line before you use it.** Take `step`, `id` and `message` from the
  decoded object; never match the raw text.
- **Run `ask` through `docker exec` exactly as shown, with no `-e` flag.** Never set
  `OPENCLAW_SHELL`.
- **Post nothing and re-index nothing.** A re-index for a revision round needs the
  owner's go-ahead at that time; ask, and wait.

## Start or resume

Do this before any `core.evaluate` call; `core.evaluate` fails on a run folder that does
not exist yet.

1. Check whether this attempt's run folder holds the book yet:
   ```
   test -d ~/.openclaw/scriptorium-eval/<run>-<label>/'<book>'
   ```
   - Exit 0: the run was prepared before. Resume: go to the loop.
   - Exit 1: a fresh run. Go to step 2.
2. Make the run folder from the bot's data (`<run>` is `bot`) or Claude's (`crosscheck`),
   with Claude's questions:
   ```
   uv run --python 3.11 --no-project python -m bot.eval_bot prepare '<book>' --from <source> --to ~/.openclaw/scriptorium-eval/<run>-<label> --questions index
   ```
   where `<source>` is `~/.openclaw/scriptorium` for `bot` and `index` for `crosscheck`.
   Exit 0: go to the loop. Any other exit: stop, and report its stderr.

## The loop

1. Run:
   ```
   uv run --python 3.11 --no-project python -m core.evaluate next '<book>' --data-dir ~/.openclaw/scriptorium-eval/<run>-<label>
   ```
   Exit 2: stop, and report its stderr.
2. On `"step": "ask"`: the driver reads the question itself. Run:
   ```
   docker exec openclaw-openclaw-gateway-1 bash -c "cd ~/code/scriptorium && python3 -m bot.eval_bot ask '<book>' --data-dir ~/.openclaw/scriptorium-eval/<run>-<label>"
   ```
   - Exit 0: the answer is saved. Print `<id> answered`, and go to step 1.
   - Any other exit: stop, and report the id, the exit code and the last line it printed.
     Exit 3 means the bot replied with a setup line instead of an answer; exit 4 means
     the answer is saved but its tool calls could not be checked. Save nothing yourself,
     and never save an error as an answer.
3. On `"step": "judge"`: dispatch a **new** `judge` sub-agent whose message is the decoded
   `message`, exactly. Wait for its reply, then save it exactly as received:
   ```
   uv run --python 3.11 --no-project python -m core.evaluate judge '<book>' '<id>' --data-dir ~/.openclaw/scriptorium-eval/<run>-<label> <<'END_OF_JUDGEMENT'
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
     `/evaluate-bot <book> <run> [label]` again resumes at this judge step.
   - Exit 2: stop, and report its stderr.
4. On `"step": "done"`: go to the score.

## The score

1. Run:
   ```
   uv run --python 3.11 --no-project python -m core.evaluate score '<book>' --data-dir ~/.openclaw/scriptorium-eval/<run>-<label>
   ```
   Exit 0 and exit 1 both mean it scored. Its line carries Claude's own bar (36/40); it is
   not the verdict here, so do not stop on exit 1. Exit 2: stop, and report its stderr.
2. Keep this attempt's score for the stop rule, under its run and label, so a `crosscheck`
   score never replaces a `bot` one:
   ```
   mkdir -p ~/.openclaw/scriptorium-eval/'<book>'-scores && cp ~/.openclaw/scriptorium-eval/<run>-<label>/'<book>'/eval/score.json ~/.openclaw/scriptorium-eval/'<book>'-scores/<run>-<label>.json
   ```
3. Get the verdict from `core.compare`.
   - `<run>` is `bot`:
     ```
     uv run --python 3.11 --no-project python -m core.compare bar index/'<book>'/eval/score.json ~/.openclaw/scriptorium-eval/<run>-<label> '<book>'
     ```
     Exit 0 (pass) or 1 (fail) prints the line and writes `compare.json` beside the run's
     `score.json`. Exit 2: stop, and report its stderr.
   - `<run>` is `crosscheck`:
     ```
     uv run --python 3.11 --no-project python -m core.compare crosscheck index/'<book>'/eval/score.json ~/.openclaw/scriptorium-eval/<run>-<label>/'<book>'/eval/score.json
     ```
     It prints a line whose first word is `index` or `answering`.
4. For a `bot` run whose `<label>` is not `baseline`, also run the stop rule. Pass Claude's
   current `score.json` first (after an index-side round has re-scored Claude, that is the
   new one), then the kept `bot` scores in order (`bot-baseline.json`, then `bot-r1.json`,
   ... up to this label). For `r1`:
   ```
   uv run --python 3.11 --no-project python -m core.compare rounds index/'<book>'/eval/score.json ~/.openclaw/scriptorium-eval/'<book>'-scores/bot-baseline.json ~/.openclaw/scriptorium-eval/'<book>'-scores/bot-r1.json
   ```
   It prints `continue` (exit 0) or `stop: <reason>` (exit 1). Exit 2: stop, and report
   its stderr.
5. Draft the tracker comment, and do not post it. Write the verdict line, then the
   `rounds` line if there is one, to `.claude/handoffs/evaluate-bot/<book>-<run>-<label>-comment.md`.
6. Print the verdict line, exactly, as the last thing you say, then the path of the draft.

## When you stop early

Report in a few lines: what this run did, where it stopped and why, and the command that
continues it (`/evaluate-bot <book> <run> [label]`). A stopped run leaves no score and no
draft.
