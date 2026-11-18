"""Validate one section worker's reply against the settled schema.

    top:    {"section": str, "scenes": [scene]}
    scene:  {"scene": int, "pov": str|null, "summary": str, "events": [str],
             "characters": [char], "places": [str], "terms": [term], "open_questions": [str]}
    char:   {"name": str, "line": str, "new": bool} + optional "aka" and "quote", both or neither
    term:   {"term": str, "meaning": str}

Errors reject the reply and go verbatim into the retry prompt; warnings only go to a log.
"""

from __future__ import annotations

import json
import re

TOP_FIELDS = ("section", "scenes")
SCENE_FIELDS = ("scene", "pov", "summary", "events", "characters", "places", "terms", "open_questions")
CHARACTER_FIELDS = ("name", "line", "new")
CHARACTER_OPTIONAL = ("aka", "quote")
TERM_FIELDS = ("term", "meaning")
SUMMARY_MIN_WORDS = 8
SUMMARY_MAX_WORDS = 120

_FENCE = re.compile(r"```json[ \t]*\n(.*)\n```", re.DOTALL)
_WORD = re.compile(r"[^\W_]+")
_QUOTES = str.maketrans({c: '"' for c in "“”„‟″"} | {c: "'" for c in "‘’‚‛′"})


def normalise(text: str) -> str:
    """Collapse whitespace and fold curly/prime quote marks to ASCII; case is kept."""
    return " ".join(text.translate(_QUOTES).split())


def parse_reply(text: str) -> tuple[dict | None, list[str]]:
    """The reply as a JSON object, unwrapping one ```json fence; or (None, errors)."""
    body = text.strip()
    fenced = _FENCE.fullmatch(body)
    if fenced:
        body = fenced.group(1)
    try:
        obj = json.loads(body)
    except json.JSONDecodeError as e:
        return None, [f"the reply is not valid JSON ({e.msg} at line {e.lineno} column {e.colno}); "
                      "reply with exactly one JSON object and nothing else"]
    if not isinstance(obj, dict):
        return None, ["the reply must be one JSON object"]
    return obj, []


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_str_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def _blank(value: object) -> bool:
    return isinstance(value, str) and not value.strip()


def _fields(obj: dict, required: tuple[str, ...], optional: tuple[str, ...], where: str) -> list[str]:
    errors = [f'{where}missing field "{f}"' for f in required if f not in obj]
    errors += [f'{where}unknown field "{k}"' for k in obj if k not in required and k not in optional]
    return errors


def _check_character(char: object, where: str, names: set[str], aliases: dict[str, str],
                     scene_text: str | None) -> list[str]:
    if not isinstance(char, dict):
        return [f"{where}must be an object"]
    errors = _fields(char, CHARACTER_FIELDS, CHARACTER_OPTIONAL, where)
    for field in ("name", "line", *CHARACTER_OPTIONAL):
        if field in char and not isinstance(char[field], str):
            errors.append(f'{where}"{field}" must be a string')
    if "new" in char and not isinstance(char["new"], bool):
        errors.append(f'{where}"new" must be true or false')
    for field in ("name", *CHARACTER_OPTIONAL):
        if _blank(char.get(field)):
            errors.append(f'{where}"{field}" is empty')
    if ("aka" in char) != ("quote" in char):
        errors.append(f'{where}"aka" and "quote" go together; give both or neither')
    if errors:
        return errors

    name = char["name"]
    if name in names:
        if char["new"] is not False:
            errors.append(f'{where}"{name}" is in the brief; set "new": false')
    elif name in aliases:
        errors.append(f"{where}\"{name}\" is an alias in the brief; use the brief's name \"{aliases[name]}\"")
    elif char["new"] is not True:
        errors.append(f'{where}"{name}" is not in the brief; set "new": true')

    if "quote" in char and scene_text is not None and normalise(char["quote"]) not in normalise(scene_text):
        errors.append(f'{where}"quote" is not in this scene\'s text; quote one contiguous stretch verbatim')
    return errors


def _check_term(term: object, where: str) -> list[str]:
    if not isinstance(term, dict):
        return [f"{where}must be an object"]
    errors = _fields(term, TERM_FIELDS, (), where)
    errors += [f'{where}"{f}" must be a string' for f in TERM_FIELDS if f in term and not isinstance(term[f], str)]
    if _blank(term.get("term")):
        errors.append(f'{where}"term" is empty')
    return errors


def _check_scene(scene: object, i: int, scene_texts: list[str], names: set[str],
                 aliases: dict[str, str], chapter0: bool) -> list[str]:
    if not isinstance(scene, dict):
        return [f"scenes[{i}]: must be an object"]
    raw = scene.get("scene")
    number = raw if isinstance(raw, int) and not isinstance(raw, bool) and 1 <= raw <= len(scene_texts) else None
    where = f"scene {number}: " if number is not None else f"scenes[{i}]: "
    errors = _fields(scene, SCENE_FIELDS, (), where)
    if "scene" in scene and not _is_int(raw):
        errors.append(f'{where}"scene" must be an integer')
    if "pov" in scene and not (scene["pov"] is None or isinstance(scene["pov"], str)):
        errors.append(f'{where}"pov" must be a name or null')
    if "summary" in scene:
        if not isinstance(scene["summary"], str):
            errors.append(f'{where}"summary" must be a string')
        elif not chapter0 and _blank(scene["summary"]):
            errors.append(f'{where}"summary" is empty')
    for field in ("events", "places", "open_questions"):
        if field in scene and not _is_str_list(scene[field]):
            errors.append(f'{where}"{field}" must be a list of strings')
    if not chapter0 and scene.get("events") == []:
        errors.append(f'{where}"events" is empty')

    scene_text = scene_texts[number - 1] if number is not None else None
    for field in ("characters", "terms"):
        if field in scene and not isinstance(scene[field], list):
            errors.append(f'{where}"{field}" must be a list')
    if isinstance(scene.get("characters"), list):
        for j, char in enumerate(scene["characters"]):
            errors += _check_character(char, f"{where}characters[{j}]: ", names, aliases, scene_text)
    if isinstance(scene.get("terms"), list):
        for j, term in enumerate(scene["terms"]):
            errors += _check_term(term, f"{where}terms[{j}]: ")
    return errors


def _lossy(old: str, new: str) -> bool:
    """Whether a re-listed meaning looks like it dropped the old one rather than restating it.

    A meaning replaces the old one in the brief, so a bare update ("also ...") or a much
    thinner one loses what was established. Same-length rewording is not lossy.
    """
    old_words = _WORD.findall(old.casefold())
    new_words = _WORD.findall(new.casefold())
    if new_words[:1] == ["also"]:
        return True
    distinct = set(old_words)
    kept = distinct & set(new_words)
    return 2 * len(kept) < len(distinct) or 2 * len(new_words) < len(old_words)


def _warnings(record: dict, brief: dict, chapter0: bool) -> list[str]:
    glossary = {g["term"].casefold(): g["meaning"] for g in brief.get("glossary") or []}
    warnings: list[str] = []
    for scene in record["scenes"]:
        where = f"scene {scene['scene']}: "
        words = len(scene["summary"].split())
        if not (chapter0 and words == 0) and not SUMMARY_MIN_WORDS <= words <= SUMMARY_MAX_WORDS:
            warnings.append(f"{where}summary has {words} words (expected {SUMMARY_MIN_WORDS}-{SUMMARY_MAX_WORDS})")
        if not scene["characters"]:
            warnings.append(f"{where}no characters")
        for term in scene["terms"]:
            old = glossary.get(term["term"].casefold())
            if old is not None and _lossy(old, term["meaning"]):
                warnings.append(f'{where}term "{term["term"]}" redefined: brief says "{old}", '
                                f'reply says "{term["meaning"]}"')
    return warnings


def validate(reply_text: str, *, section: str, scene_texts: list[str], brief: dict,
             chapter0: bool) -> tuple[dict | None, list[str], list[str]]:
    """(record, errors, warnings). record is None iff errors; warnings only for an accepted record.

    scene_texts holds one text per expected scene (K = its length), the section heading
    already folded into scene 1. chapter0 exempts only the empty summary/events rule.
    """
    record, errors = parse_reply(reply_text)
    if record is None:
        return None, errors, []
    errors = _fields(record, TOP_FIELDS, (), "")
    if "section" in record and record["section"] != section:
        errors.append(f'"section" is {json.dumps(record["section"], ensure_ascii=False)}; '
                      f'expected {json.dumps(section, ensure_ascii=False)}')
    scenes = record.get("scenes")
    if "scenes" in record and not isinstance(scenes, list):
        errors.append('"scenes" must be a list')
    elif isinstance(scenes, list):
        expected = list(range(1, len(scene_texts) + 1))
        got = [s.get("scene") if isinstance(s, dict) else None for s in scenes]
        if got != expected:
            errors.append(f"scenes must be numbered {expected} in order; got {json.dumps(got)}")
        names = {c["name"] for c in brief.get("characters") or []}
        aliases = {a: c["name"] for c in brief.get("characters") or [] for a in c.get("aka") or []}
        for i, scene in enumerate(scenes):
            errors += _check_scene(scene, i, scene_texts, names, aliases, chapter0)
    if errors:
        return None, errors, []
    return record, [], _warnings(record, brief, chapter0)
