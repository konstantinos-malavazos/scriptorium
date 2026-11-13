# scriptorium

Turns books into indexes an LLM can use.

- **Path 2, novels (v1, building now):** a spoiler-safe reading companion. Index a novel chapter by chapter, then ask "who is this?" or "what happened there?" or just discuss the book, only up to where you are.
- **Path 1, technical books (later):** turn a technical book into a Claude Code skill.

Personal tool. Not for publishing book content: indexes stay on my machine and are never committed.

Test book: *Hive* by Dan Abnett (EPUB). Two print books merged into one audiobook, large cast, lots of invented slang.

---

## How indexing works (`/index-book`)

1. **Split.** A Python script splits the EPUB into chapter files, numbered 1 to N across the whole book (Hive's two print books become one sequence).
2. **Extract, one chapter at a time.** Chapters run sequentially. Each worker gets:
   - its chapter text
   - a "story so far" brief built by code from earlier outputs: every character with aliases and one line each, the glossary, the last 3 chapter summaries

   The brief only contains the past, so it is spoiler-safe and stays small (~5 to 10K tokens) however deep into the book we are.
3. **Validate.** A script fails the run if any chapter output is missing or malformed.
4. **Merge.** A script builds character files, the glossary and the timeline from all chapter outputs.

### What each chapter worker writes

```
chapter: 23 (Book 1, ch 11)
pov: Marta
summary: Marta hands the key to Kell at the water tower.
events:
  - Marta meets Kell under the water tower
  - She gives him the key
  - Kell disappears into the lower levels
characters:
  - Marta: has the key, trusts Kell
  - Kell (aka "the grey-coated man"): old smuggler, first appearance
places: water tower, lower levels
terms:
  - scrapper: slang for a scavenger kid
open_questions:
  - What does the key open?
```

(Made-up example, not real Hive content.)

### Character files are timelines

```
Kell
- ch 23: introduced, old smuggler, takes the key from Marta
- ch 31: seen in the lower levels with ...
```

---

## Asking and discussing

- **One Discord thread per book** in `#books`, created by the bot when indexing finishes. The thread is the book's ongoing discussion.
- **Position per book is remembered.** Every answer starts with `Up to ch 37:`. Give a chapter number or describe the last scene you remember. If the bot has no position, it asks.
- **Cut copy only.** The thread only ever loads index files up to your position. It cannot leak what it never saw. Moving forward ("I'm at ch 45 now") reloads the cut.
- **Every answer names its chapter** (`ch 23: ...`), so claims are checkable.
- **No outside knowledge by default.** If it's not in the index, the answer is "you haven't got there yet" or "I don't know".
- **Web search only on request** (`/web ...`). A sub-agent searches and filters out plot spoilers given your position. The thread only receives the filtered answer, labelled `[web, filtered]`. Raw results never enter the thread's session.

Commands:

```
/index-book <epub>                      index a book, open its thread
/ask-book <book> [chN] <question>       one-off question (asks for position if missing)
/web <question>                         spoiler-filtered web lookup, in a book thread
```

---

## Evaluator

Goal: a number, not a feeling. "Claude 37/40, bot 31/40, misses minor characters."

- Claude reads the **raw book** (whole, 1M context), never the index, and writes ~40 questions.
- Every question carries a short **exact quote** from the book as evidence. A script checks the quote exists in the EPUB text. No match, question dropped.
- Question types: who is X, what happened at Y, what does term Z mean, and **spoiler traps** (asked at ch 20 about ch 50; correct answer is "not yet").
- Optional: I spot-check a few.

---

## Build order

1. **Claude Code version.** Scripts + Claude Code sub-agents as chapter workers. Index Hive.
2. **Evaluator.** Generate and quote-verify the question set. Score the Claude Code index.
3. **Bot version.** Same scripts, same worker instructions file, only the model changes. Python baked into a derived Docker image (the container's writable layer is wiped on image pulls).
4. **Compare.** Run the evaluator against the bot's index.

Shared between both versions: split, validate, merge, cut scripts, and one worker-instructions file. If the bot scores worse, the difference is the model, not the code.

## v1 is done when

- Hive is indexed in Claude Code and scores well on the evaluator
- the bot's version scores within a few points of Claude's
- I've had one real discussion in the Hive thread while listening

Then: path 1, multi-book linking.

---

## Dev setup (from claude-code-playbook)

Built with the playbook's solo path: `/charting` from this README, then `/bootstrap`, `/cut-backlog`, `/start-ticket`. No `/feeling-lucky`: early tickets need eyes on the output.

**Guardrail hooks.** This repo is public and the test book is copyrighted, so book content must never be committed.
- `.gitignore`: `books/`, `index/`, `*.epub`
- `block-book-content` hook (same pattern as the playbook's `block-*` hooks): refuses any commit that touches those paths
- Playbook hooks carried over: `block-dangerous-git`, `cleanup-handoffs`

**Forgetful.** Stores the design decisions so later sessions don't re-argue them: sequential workers with a story-so-far brief, cut copy for spoilers, quote-verified evaluator, web search via a filtering sub-agent.

**Views (`templates/views/`).** Pages to eyeball worker quality:
- one chapter's extraction next to its "story so far" brief
- a character's timeline across chapters

**Serena.** Added later, once there is real code to navigate.

---

## Open checks

- [ ] Do Audible chapter titles line up with the EPUB chapters?
- [ ] Is Python in the OpenClaw image, or does it need the derived Dockerfile?
- [ ] Does OpenClaw save Discord attachments (the EPUB) somewhere exec can read?
- [ ] Can OpenClaw spawn sub-agents? If not, the `/web` filter runs as a script that calls the API.
- [ ] Can the bot create Discord threads itself?
