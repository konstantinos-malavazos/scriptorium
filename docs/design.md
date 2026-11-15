# scriptorium — design

Turns books into indexes an LLM can use.

- **Path 2, novels (v1):** a spoiler-safe reading companion. Index a novel section by
  section, then discuss it in its own Discord thread from where you are, spoiler-free.
- **Path 1, technical books (later):** turn a technical book into a Claude Code skill.

Personal tool. Book content and indexes stay on the owner's machine and are never committed.

This file is the design as charted. Each decision was made on a ticket of the
[map](https://github.com/konstantinos-malavazos/scriptorium/issues/1), and the `#N` links
point to them. Where this file and a closed ticket disagree, the ticket wins.

**Test books** (both EPUB, git-ignored in `books/`):
- *Hive* (Dan Abnett): one book, 13 chapters plus 5 interludes in 4 parts, about 210K words
  of story, a large cast and lots of invented slang.
- *Children of Dune* (Frank Herbert), second, after Hive passes: no chapters, 64 sections
  split by `=====` lines, each opening with an epigraph.

---

## Words

These names are fixed; `CLAUDE.md` carries the same list.

- **section**: a unit of the story in reading order, labelled as the book prints it, with a
  hidden running order number. Hive: `ch 1`..`ch 13`, plus the five interludes by name
  ("The Hive Shakes"). Children of Dune: `§1`..`§64`, numbered by the bot and labelled by
  the epigraph's source.
- **chapter**: only what the book itself numbers as one. Never renumbered.
- **scene**: the stretch between the book's own scene breaks. A Children of Dune § is one
  scene.
- **position**: the section and scene the reader has reached.
- **cut copy**: the index files up to the position.
- **chapter 0**: the non-story sections marked "always" at split time, by the split's
  default or by an optional owner flip (a cast list, a reference glossary). They are in
  every cut.
- **brief**: the code-built story-so-far that a section worker gets. Past only.
- **recap**: what a returning reader gets. It is built by the same brief-builder at the
  reader's position.

Never call either of the last two "story so far" on its own.

Any medium counts: reading the EPUB, listening on Audible, discussing one scene, or coming
back after months. Positions and commands use the book's own terms. Audible is one input,
never the frame. Audible's chapters match Hive's EPUB one to one, and each one except
ch 13 is split into two ~1h tracks
([#3](https://github.com/konstantinos-malavazos/scriptorium/issues/3)).

---

## Indexing

1. **Split** ([#16](https://github.com/konstantinos-malavazos/scriptorium/issues/16)).
   - **One general script, no per-book code.** It finds sections from the table of
     contents or from divider lines. It finds scenes from a visible gap before a
     paragraph: in Hive, a `No_Indent` paragraph right after body text, 177 scenes in all.
   - **The split approves itself.** The script prints sections, scene counts, each scene's
     opening words, and what it takes to be non-story. It then runs its own general checks:
     no lost or duplicated text, no empty scene, unique story-section labels (non-story
     sections are not counted), no story text left in front of a later section's epigraph,
     and story being the majority of the book. It is approved only when every check
     passes; when one fails it stays unapproved and names the failed check.
     Nothing is indexed from an unapproved split. No human step is needed; validate
     ([#15](https://github.com/konstantinos-malavazos/scriptorium/issues/15)) re-checks
     counts later.
   - **Non-story sections are marked "always" or "drop"**
     ([#5](https://github.com/konstantinos-malavazos/scriptorium/issues/5)). Before the
     first story section, the cover, the contents page and empty pages default to "drop"
     (the contents page names later chapters, a spoiler in every cut); everything else
     defaults to "always". Text before the first section's epigraph (a title or copyright
     line) is its own non-story section and takes the same default as any other page
     before the story: "always" for a title or copyright line, but "drop" if it came from
     the cover or the contents page. Part pages inside the story and everything after the
     last story section default to "drop". The owner may flip any mark afterwards; a flip
     re-runs the checks and can never approve a split that fails them. Re-running the
     split resets flips to the defaults and names the reset ones. "Always" sections become
     chapter 0. "Drop" sections are never extracted.
   - **An opening quote (epigraph) is a heading, not a scene.**
   - **Lost paragraph breaks stay lost.** Children of Dune has none: each § is one block
     of text.
2. **Extract, one section at a time, in order.** Each worker gets the section text and a
   brief, built by code from earlier outputs: every character with aliases and one line
   each, the glossary, and the last 3 section summaries. The brief holds only the past, so
   it is spoiler-safe and stays small however deep the book goes. Chapter 0 runs first, so
   its names seed the first brief.
3. **Validate** ([#15](https://github.com/konstantinos-malavazos/scriptorium/issues/15)).
   On a rejection, the worker gets one retry in a fresh session, with the error list added
   to its prompt. If that fails too, the run stops. Both rejected replies are saved beside
   the section, and a re-run resumes at the first missing section.
4. **Merge.** A script builds character timelines, the glossary and the story timeline
   from the section outputs. These per-section outputs are the only stored truth.

### What a section worker writes

One JSON object, read with stdlib `json`. The scenes hold every fact. There is no
section-wide summary, so a cut at scene 3 keeps scenes 1–3 untouched.

```json
{
  "section": "ch 6",
  "scenes": [
    {
      "scene": 1,
      "pov": "Marta",
      "summary": "Marta hands the key to Kell at the water tower.",
      "events": ["Marta meets Kell under the water tower", "She gives him the key"],
      "characters": [
        {"name": "Marta", "line": "has the key, trusts Kell", "new": false},
        {"name": "Kell", "line": "old smuggler, takes the key", "new": true,
         "aka": "the grey-coated man", "quote": "…"}
      ],
      "places": ["water tower", "lower levels"],
      "terms": [{"term": "scrapper", "meaning": "slang for a scavenger kid"}],
      "open_questions": ["What does the key open?"]
    }
  ]
}
```

(A made-up example, not real Hive content. #15 fixes the fields; `aka` sits on the
character entry, always together with its `quote`.)

- **Names.** A character already in the brief uses the brief's exact name. A new one gets
  `"new": true` and the name the text uses.
- **Character lines.** Each line says what the character is or does *as of that scene*.
  The merge stacks them into a timeline, and a recap takes the latest line at or before
  the reader's scene.
- **`aka` only when the text states the link,** and it carries the quote that states it.
  Hints go under `open_questions`. The merge records the section where each `aka` first
  appears.

**The validator rejects** any of:
1. Invalid JSON, or a missing, wrong-typed or unknown field.
2. The wrong section, or scenes that are not exactly 1..K, where K is the split's count.
3. A scene with no summary or no events. Chapter 0 is exempt from this one.
4. A name missing from the brief without `"new": true`, or marked new while it is in the
   brief.
5. An `aka` whose quote is not in its scene. Whitespace and quote marks are normalised
   first.

**Warnings only** go to a log beside the section: a summary far off length, a scene with
no characters, a term that contradicts the glossary
([#17](https://github.com/konstantinos-malavazos/scriptorium/issues/17)).

### Character files are timelines

```
Kell
- ch 6 · scene 1: old smuggler, takes the key from Marta
- ch 9 · scene 4: seen in the lower levels with …
```

---

## Reading position

Decided in [#4](https://github.com/konstantinos-malavazos/scriptorium/issues/4).

- **A position is a section plus a scene,** in the book's own labels.
- **Only the reader moves it,** forward or back. Re-reading shrinks the cut. Mentioning a
  place never moves it: a place past the position gets "that's past where you are, move
  there?", and nothing is searched until the reader says yes. An unknown label gets the
  list of accepted labels.
- **A bare section makes the bot ask where in it.** For "I'm at ch 6", it asks: just
  started, finished, or describe the last scene. It answers nothing from ch 6 until the
  reader says.
- **A described scene is matched within a limit.** The bot searches only up to the
  remembered position plus the current section, or a rough marker the reader gives ("part
  two", "about 40%"). With no limit known, it asks for one first. It shows a match as its
  label plus the scene's first sentence, and the reader confirms.
- **The position is stored in a file,** not in the thread session.
- **Every answer opens with the position:** `Up to ch 6 · scene 3:`.

## Spoiler safety

Decided in [#5](https://github.com/konstantinos-malavazos/scriptorium/issues/5),
[#8](https://github.com/konstantinos-malavazos/scriptorium/issues/8) and
[#19](https://github.com/konstantinos-malavazos/scriptorium/issues/19).

- **The cut is a re-merge.** `cut <book> <position>` runs the shared merge over sections
  1..position, plus the scenes up to the position in the current section, plus chapter 0.
  Files merged from the whole book are never served. The cut is code with no model, so
  rebuilding it costs nothing.
- **Setting the position rebuilds `cut/`.** There is no separate reload step.
- **The thread reads the cut from disk for every question,** through the cut script,
  taking only what the question needs. It never holds the cut in its session: compaction
  and the 240-minute idle reset would drop it anyway.
- **The fence is a skill rule:** only the cut script, never `index/`, `sections/` or the
  EPUB. The evaluator's traps test it.
- **v1 runs book threads on the bot's normal OpenClaw agent.** Two things would bring back
  a dedicated, sandboxed agent, each as a new grilling ticket:
  - a trap leak where the session log shows a read outside `cut/`;
  - [#18](https://github.com/konstantinos-malavazos/scriptorium/issues/18)'s last resort,
    a model used only for scriptorium.

## Asking and discussing

- **One Discord thread per book** in `#books📖`, named `<Title> — <Author>`.
  `new_thread.py` creates it through `openclaw message thread create` (run through exec) at
  the *start* of indexing. In the same step it writes `threads.json`, which maps thread id
  to book. The bot gets no `message` tool
  ([#11](https://github.com/konstantinos-malavazos/scriptorium/issues/11)).
- **Indexing progress is posted in the thread** ("ch 3 done (3/18)"), and it ends with
  "Ready. Where are you?" A question asked during indexing gets "still indexing, at ch N".
- **Every answer names its section** (`ch 9: …`), so each claim can be checked.
- **No outside knowledge by default.** If something is not in the cut, the answer is "you
  haven't got there yet" or "I don't know".
- **Recap** ([#13](https://github.com/konstantinos-malavazos/scriptorium/issues/13)), on
  "refresh me", "where were we?" or `/recap`:
  - **It confirms the position first,** because a stale position is a spoiler.
  - **It is a funnel:** one paragraph on the whole story so far, then the last 3 sections
    in detail up to the reader's scene, then who is in play.
  - **Who's in play:** key characters, meaning the POV of any of the last 3 sections or
    anyone in 2+ of them, get a line each. Everyone else goes on one "Also around: …"
    line.
  - **About one screen** (300–400 words).
  - **Code gathers the material** from the cut, using the brief-builder at the reader's
    position. The bot writes cited prose from that material only. Follow-ups zoom in
    ("more on ch 9", "who's Kell again?").

## Evaluator

Decided in [#6](https://github.com/konstantinos-malavazos/scriptorium/issues/6). The goal is
a number, not a feeling.

- **The question writer reads the raw book whole,** never the index. Per book it writes 40
  accuracy questions (about 13 who, 13 what happened, 14 what a term means) and 10 spoiler
  traps.
- **At least 4 traps are alias traps.** Each asks about an alias before its reveal; a
  correct answer does not name the hidden identity. The writer spreads positions across
  early, middle and late.
- **Every question carries an exact quote,** which a script checks against the EPUB text.
  If the quote is not there, the question is dropped.
- **A fresh Claude judge marks each answer, without the book.** It gets the question, the
  position, the expected answer and the verified quote, and marks right or wrong, with no
  partial credit. Right means the expected facts, nothing false, and a citation of a
  section that holds the fact.
- **Leaks are counted apart from accuracy.** One leak fails the index whatever its score.
- **Recap leak checks.** At 3 positions per book the evaluator requests a recap, and the
  judge flags any fact or citation past the position. The bar is 0 leaks; recap accuracy
  is not scored.
- **The bars.**
  - Claude Code index: at least 36/40, with 0 leaks.
  - Bot: at least Claude's score minus 3 on the same set, with 0 leaks.
  - Both books, Hive and then Children of Dune. Each is indexed alone, with no
    cross-book knowledge.
- **The evaluator is the only quality gate.** Nobody reviews output by eye.

### When the bot misses its bar

Decided in [#18](https://github.com/konstantinos-malavazos/scriptorium/issues/18).

1. **Cross-check first.** The bot's model answers the same questions from *Claude's*
   index. If it gets close to Claude's score, the bot's index lost the points; if not, its
   answering did.
2. **Revise that side's instructions.** The index side is the shared worker-instructions
   file; the answer side is the bot's skill rules. A change to anything both versions use
   re-indexes and re-scores both of them.
3. **At most 3 rounds,** stopping early on a round with no gain or with a new leak.
4. **Still missing:** a new grilling ticket, carrying the scores. A scriptorium-only model
   is the last resort.

## Models

Decided in [#7](https://github.com/konstantinos-malavazos/scriptorium/issues/7). Pinned in
each agent file, so that a score change can be traced to the instructions.

| Role | Model | Effort |
|---|---|---|
| Claude Code section workers | Opus 5.5 (`claude-opus-5-5`) | `medium` |
| Evaluator question writer | Fable 5.2 | `high` |
| Evaluator judge | Opus 5.5 | `medium` |
| Bot version (indexing and answering) | DeepSeek V4 Flash, as the bot is configured | as configured |
| Brief-builder, merge, cut | code, no model | — |

---

## The bot side

Decided in [#2](https://github.com/konstantinos-malavazos/scriptorium/issues/2),
[#8](https://github.com/konstantinos-malavazos/scriptorium/issues/8) and
[#11](https://github.com/konstantinos-malavazos/scriptorium/issues/11).

- **Code** runs from the bot's own clone pinned to `main`
  (`~/.openclaw/tools/scriptorium`), never from the live dev clone.
- **Data** lives in `~/.openclaw/scriptorium/<book>/`: the EPUB (copied out of
  `media/inbound/` first), section outputs, the full index, `cut/` and the position file.
  The scripts take `--data-dir`. Keeping this outside the workspace hides the full index
  from the bot's memory tools.
- **Instructions:** `bot/SKILL.md` is linked into the workspace `skills/`. One AGENTS.md
  line makes it apply in every thread listed in `threads.json`.
- **Indexing loop:** `index_bot.py` builds each section's brief and writes the prompt to a
  file. It runs `openclaw agent --message-file … --session-id <book>-s<NN> --json`, a fresh
  session per section, then validates and saves each reply, and resumes at the first
  missing section. The loop lives in code; only the model call goes through OpenClaw.
- **`bookkit`, the bot's earlier book tool, is retired when the bot version ships.** Its
  workspace files are deleted then, with the owner's approval at that time.

## Build order

1. **Claude Code version.** Scripts plus Claude Code sub-agents as section workers. Index
   Hive.
2. **Evaluator.** Generate and quote-verify the question set, and score the Claude Code
   index. Below the bar: revise the worker instructions and re-index before going on.
3. **Bot version.** The same scripts and the same worker-instructions file; only the model
   changes.
4. **Compare.** Run the evaluator against the bot's index.

Both versions share the split, validate, merge and cut scripts, the brief-builder, and the
worker-instructions file. If the bot scores worse, the difference is the model, not the
code.

## v1 is done when

From [#14](https://github.com/konstantinos-malavazos/scriptorium/issues/14), the smallest
version:

> Index a novel from its EPUB, then discuss it in its own Discord thread from my current
> position, spoiler-free: every answer names its section, `/recap` works, and the evaluator
> shows the bot within 3 of Claude Code with 0 leaks, on Hive and then Children of Dune.

## Out of scope for v1

- **`/web`**, a spoiler-filtered web lookup run on request. A sub-agent searches and
  filters out plot spoilers given the position, and the thread receives only the filtered
  answer. It is the first feature after v1 (via `/add-feature`); how the filter is built
  is still open.
- **Path 1:** technical books turned into Claude Code skills.
- **Linking across books.**
- **Publishing or sharing** any book content or index.

---

## Dev setup

The stack, the commands and the layer chain (`core → prompts → bot`) are in
[`CLAUDE.md`](../CLAUDE.md). The reasoning behind them is in
[#9](https://github.com/konstantinos-malavazos/scriptorium/issues/9).

- **Stdlib Python 3.11.** The scripts run unchanged in the stock OpenClaw image, so there
  is no derived Docker image.
- **Built with the playbook's solo path:** `/charting`, `/bootstrap`, `/cut-backlog`, then
  `/start-ticket` per work unit.
- **Book content is never committed.** `.gitignore` covers `books/`, `index/` and `*.epub`,
  and the committed `hooks/pre-commit` hook (enabled once per clone with
  `git config core.hooksPath hooks`) refuses any commit that stages them.
- **Serena** indexes the Python code (verdict yes). Markdown under `prompts/` is outside
  the index.
- **Forgetful** holds the project memory and the stack ruling. The decisions themselves
  live on the map's closed tickets.
