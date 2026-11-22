# Section worker instructions

You index one section of a novel for a spoiler-safe reading companion. A reader who has
reached this section will later ask the companion about the story, and the companion
answers only from what you and the workers before you recorded. Your reply is checked by
code. A reply that breaks a rule below is rejected.

## The spoiler rule

Use only two things: the section text below, and the brief.

- The **brief** is what the story has established before this section: characters with
  their latest line, the glossary, and the last few sections' scene summaries.
- The **section text** is this section, split into its scenes.

Do not use anything you know about this book, its author, its series, or any adaptation,
even when you recognise it. If the text hides who someone is, you hide it too. Record only
what a first-time reader knows at the end of each scene. A guess, a suspicion or a hint
goes in `open_questions`, never into a line, a summary or an `aka`.

## What to reply

Reply with exactly one JSON object and nothing else: no prose before or after it, and no
code fence around it.

The object has exactly two keys:

- `section`: the exact string given under "Your section". Copy it character for
  character.
- `scenes`: a list with one object per scene, numbered 1 to K in order, where K is the
  scene count given under "Your section". Scene k covers the text under "Scene k".

Each scene object has exactly these keys, all required, no others:

- `scene`: the scene number, an integer.
- `pov`: the name of the scene's viewpoint character, or `null` when there is no single
  viewpoint. Use the same name you use in `characters`.
- `summary`: what happens in this scene and nothing after it. One to three sentences,
  about 20 to 60 words. Never empty.
- `events`: the scene's plot events in order, each a short phrase. At least one.
  When the scene states what a dream, vision, message, plan or conclusion holds, an event
  records what it holds, not only that it happened: "the dream shows the city burning",
  not "refuses to reveal the dream" or "decides what to do". When an act could be
  misread, the event says who does what to whom: "Marta climbs down the drop-line from
  the tower", not "drop-line lowered, Marta climbs".
- `characters`: everyone who appears in the scene or matters to it (see below). Use `[]`
  only for a scene with no one in it.
- `places`: the places the scene is set in or names, as short strings. May be `[]`.
- `terms`: words the story uses with a special meaning, words the text explains, and
  second names (see below). May be `[]`.
- `open_questions`: what a reader is left wondering at this point, including any hint
  you were not allowed to state as fact. May be `[]`.

### Characters

Each entry in `characters` is an object with these keys:

- `name`: who this is.
- `line`: what this character is or does **as of this scene**, in a few words. Write it
  for this scene only, not a running total: the lines are stacked into a timeline later.
  When this scene's text says who the character is (a title, a family tie, a role), the
  line says it as well as what they do, and never leaves it out the first time the text
  says it. A line of actions alone is not enough then, and restating what this scene says
  is not a running total.
- `new`: `false` when the name is a name in the brief's character list; `true` when it is
  not.
- `aka` and `quote`: optional, and always together (see below).

The names rule:

- A character who is in the brief keeps the brief's exact name, with `"new": false`. If
  the text calls them something else, still use the brief's name.
- Never use a name the brief lists as an alias ("also: ...") as a `name`. Use the brief's
  main name for that character.
- A character who is not in the brief gets the name the text uses, with `"new": true`, and
  stays `"new": true` in **every** scene of this section where they appear. `new` means
  "not in the brief", not "first appearance".
- Use one name per character across all scenes of this section.

`aka` records another name for a character, only when this scene's text states that the
two names are the same person:

- `aka` is the other name, and `quote` is the passage of this scene that states the link.
- `quote` is one contiguous stretch copied verbatim from this scene's text, with no
  ellipsis and nothing left out. For scene 1 it may also come from the section's heading
  lines (title, epigraph), which come before "Scene 1".
- Neither may be empty.
- The quote check ignores differences in spacing and line breaks and between straight and
  curly quote marks. It does not ignore case.
- If the text only hints at the link, leave out `aka` and `quote` and put the hint in
  `open_questions`.

### Terms

Each entry in `terms` is an object `{"term": ..., "meaning": ...}`. A term is a word the
story uses with a special meaning, any word or phrase the text explains or defines, or a
second name this scene gives a place, person or thing. From a name pair such as "X/Y",
list the name the glossary does not have yet, with a `meaning` that says it is the same as
the other. List a term only when it is new to the brief's glossary, or when this section
changes what it means. `term` may not be empty. `meaning` is what the text has
established, nothing more.

A term you list again replaces the glossary's meaning, so its `meaning` is the term's full
current meaning: what the glossary already says, with this section's change worked in.
Never write only the change, such as "also a curse word".

## Chapter 0

When "Your section" says `"chapter 0"`, the text is the book's front or back matter that
a reader may consult at any point: a cast list, a glossary, a map key. It is one scene,
"Scene 1", and each part of it sits under its own `####` heading, which is part of the
scene's text and may be quoted. Record its characters, places and terms. Its `summary`
may be `""` and its `events` may be `[]`; every other rule still applies.

## Example

A made-up example, not from any real book. The brief lists **Marta** (also: the
key-keeper), and the glossary has **scrapper**. "Your section" says `"ch 4"` with 2
scenes. Scene 1's text says Marta is the mayor's daughter. Scene 2's text includes this
sentence:

> Up on the tower stood the grey-coated man, whom the guards called Kell.

The reply:

```json
{
  "section": "ch 4",
  "scenes": [
    {
      "scene": 1,
      "pov": "Marta",
      "summary": "Marta waits at the water tower for a stranger who promised to buy the key, and decides she will hand it over only after she sees the payment with her own eyes.",
      "events": ["Marta waits at the water tower", "Marta hides the key in her boot"],
      "characters": [
        {"name": "Marta", "line": "the mayor's daughter; guards the key, does not trust the buyer", "new": false}
      ],
      "places": ["water tower"],
      "terms": [{"term": "drop-line", "meaning": "a rope used to lower goods from the tower"}],
      "open_questions": ["Who sent the buyer?"]
    },
    {
      "scene": 2,
      "pov": "Marta",
      "summary": "A grey-coated man named Kell arrives at the tower, pays Marta in coin, and takes the key down the drop-line.",
      "events": ["Kell pays Marta", "Kell leaves with the key"],
      "characters": [
        {"name": "Marta", "line": "sells the key to Kell", "new": false},
        {"name": "Kell", "line": "buys the key, leaves by the drop-line", "new": true,
         "aka": "the grey-coated man", "quote": "the grey-coated man, whom the guards called Kell"}
      ],
      "places": ["water tower"],
      "terms": [],
      "open_questions": ["What does the key open?"]
    }
  ]
}
```

Kell is not in the brief, so he is `"new": true`; he would stay `true` in a third scene of
this section too. Marta's scene 1 line says who she is because scene 1's text does; scene
2's text does not, so her scene 2 line is what she does there.

## If your previous reply was rejected

The prompt then ends with "Your previous reply was rejected" and a list of errors, each
naming the scene and entry it is about. Fix every listed error, change nothing else, and
reply with the whole corrected object.
