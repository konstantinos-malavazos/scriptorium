import os
import re
from pathlib import Path

from core.extract import INSTRUCTIONS
from core.validate import CHARACTER_FIELDS, CHARACTER_OPTIONAL, SCENE_FIELDS, TERM_FIELDS, TOP_FIELDS, validate

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "prompts"
AGENT = PROMPTS / "agents" / "section-worker.md"
SKILL = PROMPTS / "skills" / "index-book" / "SKILL.md"

# The example in the instructions describes this brief and quotes this scene-2 sentence;
# change them together.
EXAMPLE_BRIEF = {
    "characters": [{"name": "Marta", "aka": ["the key-keeper"], "line": "has the key", "seen": "ch 3 · scene 1"}],
    "glossary": [{"term": "scrapper", "meaning": "a scavenger kid"}],
    "recent": [],
    "chapter0": None,
}
EXAMPLE_SCENE_2 = "Up on the tower stood the grey-coated man, whom the guards called Kell."


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


def test_claude_discovery_paths_are_relative_symlinks_to_the_prompts_sources():
    for link, source in (
        (ROOT / ".claude" / "agents" / "section-worker.md", AGENT),
        (ROOT / ".claude" / "skills" / "index-book" / "SKILL.md", SKILL),
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
