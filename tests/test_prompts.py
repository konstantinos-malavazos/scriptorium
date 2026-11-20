import json
import os
import re
from pathlib import Path

from core.brief import Position, render_recap
from core.cut import FALLBACK, check_answer, position_line
from core.evaluate import ACCURACY_KINDS, TRAP_KINDS, _judge_message, book_text, check_question, parse_judgement
from core.evaluate import parse_recap_judgement
from core.evaluate import TOP_FIELDS as EVAL_TOP_FIELDS
from core.evaluate import TOP_OPTIONAL as EVAL_TOP_OPTIONAL
from core.extract import INSTRUCTIONS
from core.merge import chapter0_tag, tag
from core.split import _unit_words, load_approved
from core.validate import CHARACTER_FIELDS, CHARACTER_OPTIONAL, SCENE_FIELDS, TERM_FIELDS, TOP_FIELDS, validate
from tests.index_fixture import fixture_split, write_split

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "prompts"
AGENT = PROMPTS / "agents" / "section-worker.md"
SKILL = PROMPTS / "skills" / "index-book" / "SKILL.md"
ANSWERER = PROMPTS / "agents" / "book-answerer.md"
ASK_SKILL = PROMPTS / "skills" / "ask-book" / "SKILL.md"
WRITER = PROMPTS / "agents" / "question-writer.md"
JUDGE = PROMPTS / "agents" / "judge.md"
RECAP_JUDGE = PROMPTS / "agents" / "recap-judge.md"
EVAL_SKILL = PROMPTS / "skills" / "evaluate-book" / "SKILL.md"
BOT_EVAL_SKILL = PROMPTS / "skills" / "evaluate-bot" / "SKILL.md"
JUDGE_DENIED = {"Read", "Write", "Edit", "NotebookEdit", "Bash", "Glob", "Grep", "WebFetch", "WebSearch",
                "Agent", "Skill", "mcp__*"}

# The example in the instructions describes this brief and quotes this scene-2 sentence;
# change them together.
EXAMPLE_BRIEF = {
    "characters": [{"name": "Marta", "aka": ["the key-keeper"], "line": "has the key", "seen": "ch 3 · scene 1"}],
    "glossary": [{"term": "scrapper", "meaning": "a scavenger kid"}],
    "recent": [],
    "chapter0": None,
}
EXAMPLE_SCENE_2 = "Up on the tower stood the grey-coated man, whom the guards called Kell."

# The recap judge's example material is render_recap of this, at ch 4 · scene 1.
EXAMPLE_RECAP = {
    "summaries": [
        {"section": "ch 1", "scenes": [
            {"scene": 1, "summary": "Marta takes over the water-tower key from her uncle. She swears to keep the "
                                    "town's water fair."},
            {"scene": 2, "summary": "A grey-coated man watches the tower from the road."}]},
        {"section": "ch 2", "scenes": [{"scene": 1, "summary": "The guards call the grey-coated man Kell."}]},
        {"section": "ch 3", "scenes": [
            {"scene": 1, "summary": "Kell offers Marta silver for the key, and she refuses."},
            {"scene": 2, "summary": "Marta hides the key in the well-house."}]},
        {"section": "ch 4", "scenes": [
            {"scene": 1, "summary": "A drought empties the cistern, and the council asks Marta to open the tower."}]},
    ],
    "recent": [
        {"section": "ch 2", "scenes": [
            {"scene": 1, "pov": "Marta", "summary": "The guards call the grey-coated man Kell.",
             "events": ["Kell asks the guards who holds the key."]}]},
        {"section": "ch 3", "scenes": [
            {"scene": 1, "pov": "Kell", "summary": "Kell offers Marta silver for the key, and she refuses.", "events": []},
            {"scene": 2, "pov": "Marta", "summary": "Marta hides the key in the well-house.", "events": []}]},
        {"section": "ch 4", "scenes": [
            {"scene": 1, "pov": "Marta",
             "summary": "A drought empties the cistern, and the council asks Marta to open the tower.",
             "events": ["Marta opens the tower at noon."]}]},
    ],
    "key": [
        {"name": "Marta", "aka": ["the key-keeper"], "line": "keeps the water-tower key", "seen": "ch 4 · scene 1"},
        {"name": "Kell", "aka": [], "line": "a grey-coated man who wants the key", "seen": "ch 3 · scene 1"},
    ],
    "also": ["Old Bren"],
}


def instructions() -> str:
    return INSTRUCTIONS.read_text(encoding="utf-8")


def frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "---", path
    end = lines.index("---", 1)
    fields = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(":")
        if sep and not line.startswith(" "):
            fields[key.strip()] = value.strip()
    return fields


def fenced(path: Path, lang: str) -> list[str]:
    return re.findall(rf"^```{lang}\n(.*?)^```$", path.read_text(encoding="utf-8"), re.DOTALL | re.MULTILINE)


def split_from_book_text(book: str, text: str) -> dict:
    """An approved split whose labelled book text is `text`, one paragraph per line, no headings."""
    units: list[dict] = []
    stories: dict[str, dict] = {}
    for block in text.strip().split("\n\n"):
        head, *paras = block.split("\n")
        label, sep, scene = head.removeprefix("== ").removesuffix(" ==").partition(" · scene ")
        if not sep:
            n = sum(u["kind"] == "non-story" for u in units) + 1
            units.append({"kind": "non-story", "id": f"x{n}", "label": label, "where": "before", "why": "before",
                          "default": "always", "decision": "always", "paragraphs": paras})
            continue
        if label not in stories:
            stories[label] = {"kind": "story", "order": len(stories) + 1, "label": label, "heading": [], "scenes": []}
            units.append(stories[label])
        stories[label]["scenes"].append({"scene": int(scene), "opening": paras[0], "paragraphs": paras})
    for unit in units:
        unit["words"] = _unit_words(unit)
    return {"book": book, "source": f"{book}.epub", "source_words": sum(u["words"] for u in units),
            "approved": True, "reasons": [], "flips": [], "units": units}


def test_default_instructions_file_is_the_prompts_file():
    assert INSTRUCTIONS.resolve() == (PROMPTS / "worker-instructions.md").resolve()
    assert INSTRUCTIONS.is_file()


def test_instructions_name_every_schema_field():
    text = instructions()
    fields = TOP_FIELDS + SCENE_FIELDS + CHARACTER_FIELDS + CHARACTER_OPTIONAL + TERM_FIELDS
    missing = [f for f in fields if f"`{f}`" not in text]
    assert not missing, f"fields the instructions never name: {missing}"


def test_instructions_example_passes_the_validator_without_warnings():
    text = instructions()
    examples = re.findall(r"^```json\n(.*?)^```$", text, re.DOTALL | re.MULTILINE)
    assert len(examples) == 1, "expected exactly one ```json example block"
    assert EXAMPLE_SCENE_2 in text
    scene_texts = ["Marta waited at the water tower with the key hidden in her boot.", EXAMPLE_SCENE_2]
    record, errors, warnings = validate(examples[0], section="ch 4", scene_texts=scene_texts,
                                        brief=EXAMPLE_BRIEF, chapter0=False)
    assert errors == []
    assert record is not None
    assert warnings == []


def test_instructions_carry_no_file_or_tool_talk():
    text = instructions().lower()
    for word in ("index/", "books/", "reply path", "prompt file", "write tool", "read tool"):
        assert word not in text, word


def test_section_worker_is_pinned_to_opus_at_medium_with_read_and_write_only():
    fields = frontmatter(AGENT)
    assert fields["name"] == "section-worker"
    assert fields["model"] == "claude-opus-5-5"
    assert fields["effort"] == "medium"
    assert fields["tools"] == "Read, Write"


def test_index_book_skill_names_itself_and_dispatches_the_section_worker():
    assert frontmatter(SKILL)["name"] == "index-book"
    body = SKILL.read_text(encoding="utf-8")
    assert "section-worker" in body
    assert "python -m core.extract next" in body
    assert "python -m core.extract submit" in body


def test_book_answerer_is_pinned_to_opus_at_medium_with_read_only():
    fields = frontmatter(ANSWERER)
    assert fields["name"] == "book-answerer"
    assert fields["model"] == "claude-opus-5-5"
    assert fields["effort"] == "medium"
    assert fields["tools"] == "Read"


def test_book_answerer_gives_the_one_fallback_verbatim():
    text = ANSWERER.read_text(encoding="utf-8")
    assert FALLBACK in text
    for phrase in ("haven't got there yet", "i don't know"):
        assert phrase not in text.lower(), phrase


def test_book_answerer_citation_examples_are_the_merge_tags():
    text = ANSWERER.read_text(encoding="utf-8")
    for citation in (tag("ch 6", 2), tag("§12", 1), chapter0_tag(["Dramatis Personae"]),
                     chapter0_tag(["Dramatis Personae", "Glossary"])):
        assert citation in text, citation


def test_book_answerer_example_answers_pass_the_check(tmp_path):
    # The examples cite ch 1..ch 3 and a chapter 0 printed as "Dramatis Personae", at ch 3
    # scene 2; the fixture split has those sections.
    split = fixture_split()
    split["units"][0]["label"] = "Dramatis Personae"
    write_split(tmp_path, "fx", split)
    approved = load_approved(tmp_path, "fx")
    text = ANSWERER.read_text(encoding="utf-8")
    examples = re.findall(r"^```text\n(.*?)^```$", text, re.DOTALL | re.MULTILINE)
    assert len(examples) == 2, "expected a cited example and a fallback example"
    for example in examples:
        assert check_answer(example, approved, Position(3, 2)) == [], example
    assert any(example.splitlines()[1:] == [FALLBACK] for example in examples)


def test_ask_book_skill_names_itself_runs_the_cut_and_checks_the_answer():
    assert frontmatter(ASK_SKILL)["name"] == "ask-book"
    body = ASK_SKILL.read_text(encoding="utf-8")
    assert "book-answerer" in body
    assert "python -m core.cut build" in body
    assert "python -m core.cut check" in body


def test_instructions_ask_a_relisted_term_for_its_full_current_meaning():
    assert "full current meaning" in " ".join(instructions().split())


def test_question_writer_is_pinned_to_opus_at_high_with_read_only():
    fields = frontmatter(WRITER)
    assert fields["name"] == "question-writer"
    assert fields["model"] == "claude-opus-5-5"
    assert fields["effort"] == "high"
    assert fields["tools"] == "Read"


def test_question_writer_example_is_a_book_file_and_its_questions_pass_the_check(tmp_path):
    books, files = fenced(WRITER, "text"), fenced(WRITER, "json")
    assert len(books) == 1 and len(files) == 1, "expected one example book file and one example reply"
    write_split(tmp_path, "example", split_from_book_text("example", books[0]))
    approved = load_approved(tmp_path, "example")
    assert book_text(approved) == books[0]
    data = json.loads(files[0])
    assert set(data) <= set(EVAL_TOP_FIELDS + EVAL_TOP_OPTIONAL)
    assert data["book"] == approved["book"]
    assert {q["kind"] for q in data["questions"]} == set(ACCURACY_KINDS + TRAP_KINDS)
    for question in data["questions"]:
        assert check_question(approved, question) == [], question["id"]


def test_judge_is_pinned_to_opus_at_medium_with_no_file_tools():
    fields = frontmatter(JUDGE)
    assert fields["name"] == "judge"
    assert fields["model"] == "claude-opus-5-5"
    assert fields["effort"] == "medium"
    assert "tools" not in fields
    denied = {t.strip() for t in fields["disallowedTools"].split(",")}
    assert JUDGE_DENIED <= denied, JUDGE_DENIED - denied


def test_judge_example_replies_pass_the_judgement_check():
    replies = fenced(JUDGE, "json")
    assert len(replies) == 2, "expected a clean example and a leak example"
    judged = []
    for reply in replies:
        obj, errors = parse_judgement(reply)
        assert errors == [], reply
        judged.append(obj)
    assert {j["leak"] for j in judged} == {True, False}


def test_judge_rule_says_omitted_secondary_detail_is_not_wrong():
    text = " ".join(JUDGE.read_text(encoding="utf-8").split())
    assert "main fact the question asks for" in text
    assert "leaving that detail out does not make the answer wrong" in text
    assert "leaves out a fact" not in text


def test_judge_rule_accepts_given_name_family_name_or_both():
    text = " ".join(JUDGE.read_text(encoding="utf-8").split())
    assert "given name, family name or both" in text


def test_question_writer_rule_names_characters_by_full_name_at_the_position():
    text = " ".join(WRITER.read_text(encoding="utf-8").split())
    assert "Name each character by the full name the book has given by that position" in text
    assert "never contains the `hidden` name" in text


def test_judge_example_messages_are_what_core_builds(tmp_path):
    write_split(tmp_path, "example", split_from_book_text("example", fenced(WRITER, "text")[0]))
    approved = load_approved(tmp_path, "example")
    messages = fenced(JUDGE, "text")[1:]
    assert len(messages) == 2, "expected a clean example message and a trap example message"
    for message in messages:
        head, answer = message.split("\nanswer:\n", 1)
        fields = dict(line.split(": ", 1) for line in head.splitlines())
        kind = "alias" if "alias" in fields else "trap" if "trap" in fields else "who"
        question = {key: fields[key] for key in fields if key != "trap"} | {"kind": kind}
        assert _judge_message(approved, question, answer) == message.strip()


def test_recap_judge_is_pinned_to_opus_at_medium_with_no_file_tools():
    fields = frontmatter(RECAP_JUDGE)
    assert fields["name"] == "recap-judge"
    assert fields["model"] == "claude-opus-5-5"
    assert fields["effort"] == "medium"
    assert "tools" not in fields
    denied = {t.strip() for t in fields["disallowedTools"].split(",")}
    assert JUDGE_DENIED <= denied, JUDGE_DENIED - denied


def test_recap_judge_flags_later_events_and_facts_the_material_does_not_hold():
    text = " ".join(RECAP_JUDGE.read_text(encoding="utf-8").split())
    assert "appears in `later:` and not in the material" in text
    assert "in neither the material nor `later:`" in text
    assert "A fact that the material holds is never a leak" in text


def test_recap_judge_example_replies_pass_the_recap_judgement_check():
    replies = fenced(RECAP_JUDGE, "json")
    assert len(replies) == 3, "expected a clean example, a later-event leak and a fact-from-nowhere leak"
    leaks = []
    for reply in replies:
        obj, errors = parse_recap_judgement(reply)
        assert errors == [] and obj is not None, reply
        leaks.append(obj["leak"])
    assert leaks == [False, True, True]


def test_recap_judge_example_message_is_what_core_builds():
    blocks = fenced(RECAP_JUDGE, "text")
    assert len(blocks) == 4, "expected the message shape, one whole example message and two other recaps"
    head, _ = blocks[1].strip().split("\nrecap:\n", 1)
    head, later = head.split("\nlater:\n", 1)
    position, material = head.split("\nmaterial:\n", 1)
    assert position == "position: ch 4 · scene 1"
    assert material == render_recap(EXAMPLE_RECAP, position_line("ch 4", 1)).strip()
    for line in later.splitlines():
        match = re.fullmatch(r"- ch (\d+) · (\d+): \S.*", line)
        assert match, line
        assert (int(match[1]), int(match[2])) > (4, 1), line


def test_recap_judge_example_recaps_pass_the_cut_check(tmp_path):
    # A recap failing the cut check is never judged, so every example recap must pass it.
    blocks = fenced(RECAP_JUDGE, "text")
    message = blocks[1].strip()
    later = message.split("\nlater:\n", 1)[1].split("\nrecap:\n", 1)[0]
    scenes = [(s["section"], c["scene"]) for s in EXAMPLE_RECAP["summaries"] for c in s["scenes"]]
    scenes += [(m[1], int(m[2])) for m in re.finditer(r"^- (.+) · (\d+): ", later, re.MULTILINE)]
    text = "\n\n".join(f"== {label} · scene {k} ==\nA line of the book." for label, k in scenes)
    write_split(tmp_path, "example", split_from_book_text("example", text))
    approved = load_approved(tmp_path, "example")
    recaps = [message.split("\nrecap:\n", 1)[1], *blocks[2:]]
    for recap in recaps:
        assert check_answer(recap, approved, Position(4, 1)) == [], recap


def test_evaluate_book_skill_names_itself_drives_the_evaluator_and_asks_through_ask_book():
    assert frontmatter(EVAL_SKILL)["name"] == "evaluate-book"
    body = EVAL_SKILL.read_text(encoding="utf-8")
    for command in ("book", "questions", "next", "answer", "judge", "score"):
        assert f"python -m core.evaluate {command}" in body, command
    for name in ("question-writer", "`judge`", "ask-book"):
        assert name in body, name
    assert "book-answerer" not in body


def test_evaluate_book_skill_stops_instead_of_saving_a_failed_answer():
    body = " ".join(EVAL_SKILL.read_text(encoding="utf-8").split())
    assert "If `answer` exits non-zero: stop" in body
    for step in ("`core.cut build`", "`core.cut check`"):
        assert step in body, step
    assert "never save an error as an answer" in body


def test_evaluate_bot_skill_names_itself_and_drives_the_bot_and_the_comparison():
    assert frontmatter(BOT_EVAL_SKILL)["name"] == "evaluate-bot"
    body = BOT_EVAL_SKILL.read_text(encoding="utf-8")
    for command in ("next", "judge", "score"):
        assert f"python -m core.evaluate {command}" in body, command
    for command in ("bar", "crosscheck", "rounds"):
        assert f"python -m core.compare {command}" in body, command
    for command in ("prepare", "ask"):
        assert f"python3 -m bot.eval_bot {command}" in body or f"python -m bot.eval_bot {command}" in body, command
    assert "`judge`" in body
    assert "book-answerer" not in body
    assert "OPENCLAW_SHELL=exec" not in body
    assert "docker exec" in body


def test_evaluate_bot_skill_does_not_stop_on_claudes_bar_and_posts_nothing():
    body = " ".join(BOT_EVAL_SKILL.read_text(encoding="utf-8").split())
    assert "do not stop on exit 1" in body
    assert "never save an error as an answer" in body
    assert "do not post it" in body
    assert "needs the owner's go-ahead" in body


def test_evaluate_bot_skill_prepares_a_fresh_run_before_any_evaluator_call():
    body = BOT_EVAL_SKILL.read_text(encoding="utf-8")
    check = "test -d ~/.openclaw/scriptorium-eval/<run>-<label>/'<book>'"
    assert check in body
    assert body.index(check) < body.index("python -m core.evaluate")
    assert body.index(check) < body.index("python -m bot.eval_bot prepare")
    assert "no questions yet" not in body


def test_evaluate_bot_skill_gives_each_attempt_its_own_run_folder_and_kept_score():
    body = BOT_EVAL_SKILL.read_text(encoding="utf-8")
    assert "--to ~/.openclaw/scriptorium-eval/<run>-<label> --questions index" in body
    assert not re.search(r"scriptorium-eval/<run>(?!-<label>)", body)
    assert "'<book>'-scores/<run>-<label>.json" in body
    assert "-scores/<label>.json" not in body


def test_evaluate_bot_skill_passes_claudes_score_first_to_the_stop_rule():
    body = BOT_EVAL_SKILL.read_text(encoding="utf-8")
    assert ("python -m core.compare rounds index/'<book>'/eval/score.json"
            " ~/.openclaw/scriptorium-eval/'<book>'-scores/bot-baseline.json") in body


def test_evaluate_bot_skill_runs_the_recaps_after_the_questions_and_drafts_their_line():
    body = BOT_EVAL_SKILL.read_text(encoding="utf-8")
    for command in ("recap-next", "recap-judge", "recap-score"):
        assert f"python -m core.evaluate {command} '<book>'" in body, command
    recap = ("docker exec openclaw-openclaw-gateway-1 bash -c \"cd ~/code/scriptorium && python3 -m bot.eval_bot recap"
             " '<book>' --data-dir ~/.openclaw/scriptorium-eval/<run>-<label>\"")
    assert recap in body
    assert "`recap-judge` sub-agent" in body
    assert body.index("python -m core.evaluate next") < body.index("python -m core.evaluate recap-next")
    assert body.index("python -m core.evaluate recap-score") < body.index("python -m core.evaluate score")
    flat = " ".join(body.split())
    assert "then the `recap-score` line" in flat
    assert "story so far" not in flat.lower()


def test_claude_discovery_paths_are_relative_symlinks_to_the_prompts_sources():
    for link, source in (
        (ROOT / ".claude" / "agents" / "section-worker.md", AGENT),
        (ROOT / ".claude" / "skills" / "index-book" / "SKILL.md", SKILL),
        (ROOT / ".claude" / "agents" / "book-answerer.md", ANSWERER),
        (ROOT / ".claude" / "skills" / "ask-book" / "SKILL.md", ASK_SKILL),
        (ROOT / ".claude" / "agents" / "question-writer.md", WRITER),
        (ROOT / ".claude" / "agents" / "judge.md", JUDGE),
        (ROOT / ".claude" / "agents" / "recap-judge.md", RECAP_JUDGE),
        (ROOT / ".claude" / "skills" / "evaluate-book" / "SKILL.md", EVAL_SKILL),
        (ROOT / ".claude" / "skills" / "evaluate-bot" / "SKILL.md", BOT_EVAL_SKILL),
    ):
        assert link.is_symlink(), link
        assert not os.path.isabs(os.readlink(link)), link
        assert link.resolve() == source.resolve(), link


def test_no_absolute_home_path_in_prompts():
    found = []
    for dirpath, _dirs, files in os.walk(PROMPTS, followlinks=False):
        for name in files:
            path = Path(dirpath) / name
            if path.is_symlink():
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "/home/" in line:
                    found.append(f"{path.relative_to(ROOT)}:{n}")
    assert not found, f"absolute home path in: {found}"
