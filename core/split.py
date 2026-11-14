"""The general EPUB split: build, self-check, persist split.json, and the load gate.

    python -m core.split outline <epub> <book> [--data-dir DIR]
    python -m core.split approve <book> --flip x<n> [x<n> ...] [--data-dir DIR]

Exit codes: 0 approved, 1 a check fails, 2 no split or unknown id (nothing written).
`approve` never approves a split whose checks fail.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from core.epub import Block, Book, read_epub

DEFAULT_DATA_DIR = "index"
CHECKS = (
    "story-found",
    "no-empty-scene",
    "labels-unique",
    "order-contiguous",
    "text-conserved",
    "no-text-before-epigraph",
    "story-majority",
    "decisions-valid",
)
DECISIONS = ("always", "drop")
NO_TOC_LABEL = "(no TOC entry)"
FRONT_MATTER_LABEL = "(front matter)"

EPIGRAPH_WINDOW = 5
MAX_SOURCE_WORDS = 15  # Children of Dune has 13-word sources
OPENING_WORDS = 12

_DIVIDER = re.compile(r"={5,}\Z")
_CHAPTER_LABEL = re.compile(r"chapter\s+(\d+)\Z", re.IGNORECASE)
_DASHES = "-–—"
# The source must start with a letter right after the dash: prose " -- ", dialogue "–’" and "-5" never match.
_INLINE_ATTRIBUTION = re.compile(r".*\s[-–—]([^\W\d_].*)\Z", re.DOTALL)
_BOOK_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_TITLE_TAGS = frozenset("h1 h2 h3 h4 h5 h6".split())
# A unit is front/back matter when every worded block carries one of these epub:types.
_MATTER_TYPES = frozenset(
    "cover titlepage halftitlepage frontmatter backmatter toc toc-brief landmarks loi lot page-list "
    "acknowledgments dedication epigraph foreword preface afterword credits credit other-credits "
    "contributors copyright-page imprint colophon seriespage errata index bibliography appendix "
    "glossary endnotes rearnotes footnotes notice abstract".split()
)


class SplitNotApproved(Exception):
    """The split is not approved, or its checks fail on the file as it stands."""


@dataclass
class _Unit:
    label: str
    blocks: list[Block]
    piece: bool = False  # cut from a unit by divider lines


def _words(text: str) -> int:
    return len(text.split())


def _label(text: str) -> str:
    text = " ".join(text.split())
    m = _CHAPTER_LABEL.match(text)
    if m:
        return f"ch {m.group(1)}"
    return text or "(untitled)"


def _toc_units(book: Book) -> list[_Unit]:
    """Each TOC entry starts a unit at its file + anchor; untitled files join the unit before."""
    stream: list[Block] = []
    starts: dict[str, int] = {}
    for file in book.spine:
        starts[file] = len(stream)
        stream.extend(book.blocks.get(file, []))
    labels: dict[int, str] = {}
    for entry in book.toc:
        if entry.file not in starts:
            continue
        pos = starts[entry.file]
        if entry.fragment:
            end = pos + len(book.blocks.get(entry.file, []))
            pos = next((i for i in range(pos, end) if entry.fragment in stream[i].anchors), pos)
        labels[pos] = _label(entry.label)  # a later entry at the same start wins (the deeper one)
    positions = sorted(labels)
    units: list[_Unit] = []
    head_end = positions[0] if positions else len(stream)
    if head_end:
        units.append(_Unit(NO_TOC_LABEL, stream[:head_end]))
    bounds = positions + [len(stream)]
    for a, b in zip(bounds, bounds[1:]):
        units.append(_Unit(labels[a], stream[a:b]))
    return units


def _strip(units: list[_Unit]) -> list[_Unit]:
    """Drop paragraphs that are only an external link (injected watermarks): not book text."""
    return [_Unit(u.label, [b for b in u.blocks if b.link != "external"], u.piece) for u in units]


def _is_divider(block: Block) -> bool:
    return bool(_DIVIDER.match(block.text))


def _cut_dividers(units: list[_Unit]) -> list[_Unit]:
    out: list[_Unit] = []
    for unit in units:
        if not any(_is_divider(b) for b in unit.blocks):
            out.append(unit)
            continue
        piece: list[Block] = []
        for block in unit.blocks:
            if _is_divider(block):
                out.append(_Unit(unit.label, piece, True))
                piece = []
            else:
                piece.append(block)
        out.append(_Unit(unit.label, piece, True))
    return out


def _body_class(units: list[_Unit]) -> str:
    counts = Counter(b.cls for u in units for b in u.blocks if b.words and b.tag == "p")
    return counts.most_common(1)[0][0] if counts else ""


def _body_words(unit: _Unit, body: str) -> int:
    return sum(b.words for b in unit.blocks if b.cls == body and b.tag not in _TITLE_TAGS)


def _is_matter(unit: _Unit) -> bool:
    worded = [b for b in unit.blocks if b.words]
    return bool(worded) and all(_MATTER_TYPES.intersection(b.types) for b in worded)


def _story_span(units: list[_Unit], body: str) -> tuple[int, int] | None:
    """The longest run (by body words) of non-matter units, trimmed to units holding body text."""
    best: tuple[int, int] | None = None
    best_words = 0
    seg_start = 0
    for i in range(len(units) + 1):
        if i < len(units) and not _is_matter(units[i]):
            continue
        bodied = [k for k in range(seg_start, i) if _body_words(units[k], body)]
        words = sum(_body_words(units[k], body) for k in bodied)
        if bodied and words > best_words:
            best, best_words = (bodied[0], bodied[-1]), words
        seg_start = i + 1
    return best


def _is_title(block: Block) -> bool:
    return block.tag in _TITLE_TAGS


def _lead_role(block: Block) -> str:
    if _is_title(block):
        return "title"
    return "other" if block.words else "blank"


def _attribution(blocks: list[Block], j: int, body: str) -> tuple[int, int, str] | None:
    """If blocks[j] attributes an epigraph, return (first epigraph block, j, source)."""
    text = blocks[j].text
    if text[0] in _DASHES:
        source = text.lstrip(_DASHES).strip()
        if not source or _words(source) > MAX_SOURCE_WORDS:
            return None
        start = j
        while start > 0 and blocks[start - 1].words and not _is_title(blocks[start - 1]) \
                and blocks[start - 1].cls != body:
            start -= 1
        if start == j and j > 0 and blocks[j - 1].words and not _is_title(blocks[j - 1]):
            start = j - 1
        return start, j, source
    m = _INLINE_ATTRIBUTION.match(text)
    if m and _words(m.group(1)) <= MAX_SOURCE_WORDS:
        return j, j, m.group(1).strip()
    return None


def _next_worded_class(blocks: list[Block], k: int) -> str | None:
    return next((b.cls for b in blocks[k + 1:] if b.words), None)


def _find_epigraph(blocks: list[Block], body: str) -> tuple[int, int, str] | None:
    """The first epigraph starting in the first EPIGRAPH_WINDOW worded, non-title blocks.
    Its standalone attribution may sit past the window (a long verse)."""
    worded = [i for i, b in enumerate(blocks) if b.words and not _is_title(b)]
    rank = {i: n for n, i in enumerate(worded)}
    for n, j in enumerate(worded):
        found = _attribution(blocks, j, body)
        if found and rank[found[0]] < EPIGRAPH_WINDOW:
            return found
        if n >= EPIGRAPH_WINDOW and blocks[j].cls == body:
            return None  # body text past the window: no epigraph can reach back into it
    return None


def _heading(blocks: list[Block], body: str) -> tuple[list[tuple[Block, str]], list[Block], str | None]:
    found = _find_epigraph(blocks, body)
    heading: list[tuple[Block, str]] = []
    source: str | None = None
    if found:
        start, end, source = found
        heading = [(b, _lead_role(b)) for b in blocks[:start]]
        heading += [(b, "epigraph") for b in blocks[start:end + 1]]
        k = end + 1
    else:
        k = 0
        while k < len(blocks) and (_is_title(blocks[k]) or not blocks[k].words):
            heading.append((blocks[k], _lead_role(blocks[k])))
            k += 1
        nxt = _next_worded_class(blocks, k) if k < len(blocks) else None
        # A subtitle sits between the title and the text; a first paragraph followed by
        # body text is the text itself, not a subtitle.
        if any(r == "title" for _, r in heading) and k < len(blocks) and blocks[k].cls != body \
                and nxt is not None and nxt != body:
            heading.append((blocks[k], "subtitle"))
            k += 1
    while k < len(blocks) and (_is_title(blocks[k]) or not blocks[k].words):
        heading.append((blocks[k], _lead_role(blocks[k])))
        k += 1
    return heading, blocks[k:], source


def _scenes(blocks: list[Block], body: str) -> list[list[str]]:
    """A scene break is a paragraph of another class straight after a body-class paragraph."""
    scenes: list[list[str]] = []
    prev: str | None = None
    for block in blocks:
        if not block.words:
            continue
        if not scenes or (block.cls != body and prev == body):
            scenes.append([])
        scenes[-1].append(block.text)
        prev = block.cls
    return scenes


def _pre_story_why(blocks: list[Block], book: Book) -> str:
    if any(b.file in book.cover_files or "cover" in b.types for b in blocks):
        return "cover"
    worded = [b for b in blocks if b.words]
    if any(b.file in book.contents_files or "toc" in b.types for b in blocks):
        return "contents"
    if worded and 2 * sum(b.link == "internal" for b in worded) >= len(worded):
        return "contents"
    return "before" if worded else "empty"


def _non_story(label: str, blocks: list[Block], where: str, book: Book) -> dict:
    why = _pre_story_why(blocks, book) if where == "before" else where
    default = "always" if why == "before" else "drop"
    paragraphs = [b.text for b in blocks if b.words]
    return {
        "kind": "non-story",
        "id": None,
        "label": label,
        "where": where,
        "why": why,
        "words": sum(_words(p) for p in paragraphs),
        "default": default,
        "decision": default,
        "paragraphs": paragraphs,
    }


def _story(label: str, heading: list[tuple[Block, str]], rest: list[Block], source: str | None,
           piece: bool, body: str) -> dict:
    head = [{"role": role, "text": b.text} for b, role in heading]
    scenes = [
        {"scene": k, "opening": " ".join(paras[0].split()[:OPENING_WORDS]), "paragraphs": paras}
        for k, paras in enumerate(_scenes(rest, body), 1)
    ]
    unit: dict = {"kind": "story", "order": None, "label": label}
    if piece and source:
        unit["source_label"] = source
    unit["words"] = sum(_words(h["text"]) for h in head) + sum(
        _words(p) for sc in scenes for p in sc["paragraphs"]
    )
    unit["heading"] = head
    if source:
        unit["epigraph_source"] = source
    unit["scenes"] = scenes
    return unit


def build_split(epub_path: str | os.PathLike[str], book: str) -> dict:
    """Build the split of one EPUB: {book, source, source_words, units}. Not yet checked."""
    src = read_epub(epub_path)
    units = _cut_dividers(_strip(_toc_units(src)))
    body = _body_class(units)
    span = _story_span(units, body)
    out: list[dict] = []
    pieces = 0
    first = True
    for i, unit in enumerate(units):
        if span is None or i < span[0]:
            where = "before"
        elif i > span[1]:
            where = "after"
        else:
            where = "inside"
        if where != "inside" or not (unit.piece or _body_words(unit, body)):
            out.append(_non_story(unit.label, unit.blocks, where, src))
            continue
        heading, rest, source = _heading(unit.blocks, body)
        if first:
            # Title/copyright lines before the first epigraph become their own front-matter unit.
            carved = [b for b, role in heading if role == "other"]
            heading = [(b, role) for b, role in heading if role != "other"]
            if carved:
                out.append(_non_story(FRONT_MATTER_LABEL, carved, "before", src))
            first = False
        label = unit.label
        if unit.piece:
            pieces += 1
            label = f"§{pieces}"
        out.append(_story(label, heading, rest, source, unit.piece, body))
    order = xid = 0
    for u in out:
        if u["kind"] == "story":
            order += 1
            u["order"] = order
        else:
            xid += 1
            u["id"] = f"x{xid}"
    return {
        "book": book,
        "source": os.path.basename(os.fspath(epub_path)),
        "source_words": sum(b.words for u in units for b in u.blocks),
        "units": out,
    }


def _unit_words(unit: dict) -> int:
    if unit.get("kind") == "story":
        return sum(_words(h.get("text", "")) for h in unit.get("heading") or []) + sum(
            _words(p) for sc in unit.get("scenes") or [] for p in sc.get("paragraphs") or []
        )
    return sum(_words(p) for p in unit.get("paragraphs") or [])


def run_checks(split: dict) -> list[str]:
    """Run the general checks on a persisted split. One "<check>: <detail>" per failure."""
    units = split.get("units") or []
    stories = [u for u in units if u.get("kind") == "story"]
    others = [u for u in units if u.get("kind") != "story"]
    reasons: list[str] = []

    if not stories:
        reasons.append("story-found: no story sections")

    empty = []
    for u in stories:
        scenes = u.get("scenes") or []
        if not scenes:
            empty.append(f"{u.get('label')} has no scenes")
        for sc in scenes:
            if not any(_words(p) for p in sc.get("paragraphs") or []):
                empty.append(f"{u.get('label')} scene {sc.get('scene')} has no words")
    if empty:
        reasons.append("no-empty-scene: " + "; ".join(empty))

    dups = [label for label, n in Counter(u.get("label") for u in stories).items() if n > 1]
    if dups:
        reasons.append("labels-unique: " + ", ".join(f"{d} appears more than once" for d in dups))

    orders = [u.get("order") for u in stories]
    if orders != list(range(1, len(stories) + 1)):
        k = next(i for i, o in enumerate(orders) if o != i + 1)
        reasons.append(f"order-contiguous: section {k + 1} in reading order has order {orders[k]}")

    total = sum(_unit_words(u) for u in units)
    source = split.get("source_words")
    if total != source:
        reasons.append(f"text-conserved: units hold {total} words, the source has {source}")

    swallowed = [u.get("label") for u in stories[1:] if any(h.get("role") == "other" for h in u.get("heading") or [])]
    if swallowed:
        reasons.append("no-text-before-epigraph: text before the epigraph in " + ", ".join(map(str, swallowed)))

    story_words = sum(_unit_words(u) for u in stories)
    if stories and isinstance(source, int) and 2 * story_words < source:
        reasons.append(f"story-majority: story holds {story_words} of {source} words")

    bad = []
    ids = Counter(u.get("id") for u in others)
    bad += [f"id {i} appears {n} times" for i, n in ids.items() if n > 1]
    bad += [f"{u.get('id')} has a malformed id" for u in others if not re.fullmatch(r"x\d+", str(u.get("id")))]
    bad += [
        f"{u.get('id')} has {field} {u.get(field)!r}"
        for u in others
        for field in ("default", "decision")
        if u.get(field) not in DECISIONS
    ]
    if bad:
        reasons.append("decisions-valid: " + "; ".join(bad))
    return reasons


def _valid_book(name: str) -> str:
    if not _BOOK_NAME.match(name):
        raise ValueError(f"book name must be letters, digits, '.', '_' or '-': {name!r}")
    return name


def _split_path(data_dir: str | os.PathLike[str], book: str) -> Path:
    return Path(data_dir) / _valid_book(book) / "split.json"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, split: dict) -> None:
    """Write atomically, so a failed write never leaves a half file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".split-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(split, f, ensure_ascii=False, indent=1)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def load_approved(data_dir: str | os.PathLike[str], book: str) -> dict:
    """The one gate later steps call: {book, sections, chapter0}, or raise.

    Raises FileNotFoundError when there is no split, SplitNotApproved when it is not
    approved or its checks fail on the file as it stands (a hand edit).
    """
    path = _split_path(data_dir, book)
    split = _read(path)
    if split.get("approved") is not True:
        raise SplitNotApproved(f"{path}: not approved: {'; '.join(split.get('reasons') or [])}")
    reasons = run_checks(split)
    if reasons:
        raise SplitNotApproved(f"{path}: checks fail: {'; '.join(reasons)}")
    units = split["units"]
    return {
        "book": split.get("book", book),
        "sections": sorted((u for u in units if u["kind"] == "story"), key=lambda u: u["order"]),
        "chapter0": [u for u in units if u["kind"] == "non-story" and u["decision"] == "always"],
    }


def format_outline(split: dict) -> str:
    lines = [f"{split['book']}: {split['source']}, {split['source_words']} words"]
    for u in split["units"]:
        if u["kind"] != "story":
            lines.append(f"   - {u['id']} [{u['decision']}] {u['why']}  {u['label']}  {u['words']}w")
            continue
        subtitle = next((h["text"] for h in u["heading"] if h["role"] == "subtitle"), None)
        extra = f"  epigraph: {u['epigraph_source']}" if u.get("epigraph_source") else (
            f"  {subtitle}" if subtitle else "")
        lines.append(f"{u['order']:>4} {u['label']}  {u['words']}w  {len(u['scenes'])} scene(s){extra}")
        for sc in u["scenes"]:
            words = sum(_words(p) for p in sc["paragraphs"])
            lines.append(f"        scene {sc['scene']:>3}  {words}w  {sc['opening']} ...")
    return "\n".join(lines)


def _verdict(split: dict) -> str:
    if split["approved"]:
        return "approved"
    return "not approved:\n" + "\n".join(f"  {r}" for r in split["reasons"])


def _outline(epub: str, book: str, data_dir: Path) -> int:
    path = _split_path(data_dir, book)
    previous = (_read(path).get("flips") or []) if path.exists() else []
    built = build_split(epub, book)
    reasons = run_checks(built)
    split = {
        "book": built["book"],
        "source": built["source"],
        "source_words": built["source_words"],
        "approved": not reasons,
        "reasons": reasons,
        "flips": [],
        "units": built["units"],
    }
    _write(path, split)
    reset = list(dict.fromkeys(previous))
    if reset:
        print("flips reset: " + " ".join(map(str, reset)))
    print(format_outline(split))
    print(_verdict(split))
    print(f"-> {path}")
    return 0 if not reasons else 1


def _approve(book: str, flips: list[str], data_dir: Path) -> int:
    path = _split_path(data_dir, book)
    if not path.exists():
        print(f"no split at {path}; run outline first", file=sys.stderr)
        return 2
    split = _read(path)
    by_id = {u.get("id"): u for u in split.get("units") or [] if u.get("kind") == "non-story"}
    wanted = list(dict.fromkeys(flips))
    unknown = [i for i in wanted if i not in by_id]
    if unknown:
        print(f"unknown non-story id(s): {' '.join(unknown)}; nothing written", file=sys.stderr)
        return 2
    for i in wanted:
        unit = by_id[i]
        unit["decision"] = "drop" if unit.get("decision") == "always" else "always"
        print(f"{i} {unit.get('label')}: now [{unit['decision']}]")
    split["flips"] = list(split.get("flips") or []) + wanted
    reasons = run_checks(split)
    split["approved"] = not reasons
    split["reasons"] = reasons
    _write(path, split)
    print(_verdict(split))
    return 0 if not reasons else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m core.split", description="Split an EPUB into an outline.")
    sub = parser.add_subparsers(dest="command", required=True)
    outline = sub.add_parser("outline", help="build, check and write the split; exit 0 approved, 1 not")
    outline.add_argument("epub", help="path to the book's EPUB")
    outline.add_argument("book", type=_valid_book, help="book name (folder under --data-dir)")
    outline.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help="parent of the per-book folders")
    approve = sub.add_parser("approve", help="flip always/drop on non-story units and re-check")
    approve.add_argument("book", type=_valid_book)
    approve.add_argument("--flip", nargs="+", required=True, metavar="x<n>", help="non-story ids to toggle")
    approve.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    args = parser.parse_args(argv)
    if args.command == "outline":
        return _outline(args.epub, args.book, Path(args.data_dir))
    return _approve(args.book, args.flip, Path(args.data_dir))


if __name__ == "__main__":
    sys.exit(main())
