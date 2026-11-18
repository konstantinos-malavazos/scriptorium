"""Read an EPUB into a `Book`: spine, TOC, cover and contents targets, and each spine file as
a flat list of `Block`s, one per paragraph-like element (never re-joined or re-split).
"""

from __future__ import annotations

import os
import posixpath
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

# Loose text directly inside a container (a div) still becomes a block of that container.
_BLOCK_TAGS = frozenset(
    "p h1 h2 h3 h4 h5 h6 li dt dd pre td th figcaption blockquote div section article aside "
    "header footer nav main figure body ul ol dl table thead tbody tfoot tr hgroup address".split()
)
# Blocks emitted even when they hold no text (an empty paragraph can carry a TOC anchor).
_EMIT_EMPTY = frozenset("p h1 h2 h3 h4 h5 h6".split())
_VOID_TAGS = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_SKIP_TAGS = frozenset("head script style title".split())
_XHTML_TYPES = frozenset({"application/xhtml+xml", "text/html"})


@dataclass(frozen=True)
class Block:
    file: str
    tag: str
    cls: str
    text: str  # whitespace-normalised, entities decoded, <br> as a space
    anchors: tuple[str, ...]  # element ids that land on this block
    types: tuple[str, ...]  # epub:type tokens of every enclosing element
    link: str  # "external" | "internal" | "none": the paragraph is only a link out/in

    @property
    def words(self) -> int:
        return len(self.text.split())


@dataclass(frozen=True)
class TocEntry:
    label: str
    file: str
    fragment: str


@dataclass(frozen=True)
class Book:
    spine: tuple[str, ...]
    toc: tuple[TocEntry, ...]
    cover_files: frozenset[str]
    contents_files: frozenset[str]
    blocks: dict[str, list[Block]]  # spine file -> its blocks, in order
    title: str = ""
    author: str = ""


def _resolve(base_file: str, href: str) -> tuple[str, str]:
    """Resolve an href found in base_file to (zip path, fragment)."""
    parts = urlsplit(href)
    path = unquote(parts.path)
    target = posixpath.normpath(posixpath.join(posixpath.dirname(base_file), path)) if path else base_file
    return target, unquote(parts.fragment)


def _is_external(href: str) -> bool:
    return bool(urlsplit(href).scheme) or href.startswith("//")


class _BlockParser(HTMLParser):
    def __init__(self, file: str) -> None:
        super().__init__(convert_charrefs=True)
        self.file = file
        self.blocks: list[Block] = []
        self.images: set[str] = set()
        self._stack: list[tuple[str, str, tuple[str, ...]]] = []  # (tag, class, epub:type tokens)
        self._skip = 0
        self._svg = 0
        self._links: list[str | None] = []  # kind of each open <a>: "external"/"internal"/None
        self._pending_ids: list[str] = []  # not `_pending`: HTMLParser has a private attribute of that name
        self._reset()

    def _reset(self) -> None:
        self._text: list[str] = []
        self._image = False
        self._link_chars = 0
        self._plain_chars = 0
        self._link_kinds: set[str] = set()

    def _owner(self) -> tuple[str, str]:
        for tag, cls, _ in reversed(self._stack):
            if tag in _BLOCK_TAGS:
                return tag, cls
        return "body", ""

    def _flush(self) -> None:
        tag, cls = self._owner()
        text = " ".join("".join(self._text).split())
        if text or self._image or tag in _EMIT_EMPTY:
            link = "none"
            if text and self._plain_chars == 0 and len(self._link_kinds) == 1:
                link = next(iter(self._link_kinds))
            types = tuple(t for _, _, ts in self._stack for t in ts)
            self.blocks.append(Block(self.file, tag, cls, text, tuple(self._pending_ids), types, link))
            self._pending_ids = []
        self._reset()

    def _note_image(self, href: str | None) -> None:
        self._image = True
        if href:
            self.images.add(_resolve(self.file, href)[0])

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if self._skip or tag in _SKIP_TAGS:
            if tag in _SKIP_TAGS:
                self._skip += 1
            return
        if self._svg:
            if tag == "svg":
                self._svg += 1
            elif tag == "image":
                self._note_image(a.get("xlink:href") or a.get("href"))
            return
        if tag == "svg":
            self._svg = 1
            self._image = True
            return
        if tag == "img":
            self._note_image(a.get("src"))
            return
        if tag == "br":
            self._text.append(" ")
            return
        if tag in _VOID_TAGS:
            return
        if tag in _BLOCK_TAGS:
            self._flush()
        if a.get("id"):
            self._pending_ids.append(a["id"] or "")
        if tag == "a":
            href = a.get("href")
            self._links.append(None if href is None else ("external" if _is_external(href) else "internal"))
        types = tuple((a.get("epub:type") or "").split())
        self._stack.append((tag, a.get("class") or "", types))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS and tag not in ("svg", "image"):
            self.handle_endtag(tag)
        elif tag == "svg" and self._svg:
            self._svg -= 1

    def handle_endtag(self, tag: str) -> None:
        if self._skip:
            if tag in _SKIP_TAGS:
                self._skip -= 1
            return
        if self._svg:
            if tag == "svg":
                self._svg -= 1
            return
        if tag in _VOID_TAGS or not any(t == tag for t, _, _ in self._stack):
            return
        if tag in _BLOCK_TAGS:
            self._flush()
        while self._stack:
            open_tag, _, _ = self._stack.pop()
            if open_tag == "a" and self._links:
                self._links.pop()
            if open_tag == tag:
                break

    def handle_data(self, data: str) -> None:
        if self._skip or self._svg:
            return
        self._text.append(data)
        chars = len("".join(data.split()))
        if not chars:
            return
        kind = self._links[-1] if self._links else None
        if kind:
            self._link_chars += chars
            self._link_kinds.add(kind)
        else:
            self._plain_chars += chars

    def close(self) -> None:
        super().close()
        self._flush()


class _NavParser(HTMLParser):
    """Collect the links of an EPUB3 nav document: (nav type, link type, href, text)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str, str, str]] = []
        self._navs: list[str] = []
        self._a: list[str] | None = None
        self._a_attrs: tuple[str, str] = ("", "")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "nav":
            self._navs.append(a.get("epub:type") or "")
        elif tag == "a" and self._navs and a.get("href"):
            self._a = []
            self._a_attrs = (a.get("epub:type") or "", a["href"] or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "nav" and self._navs:
            self._navs.pop()
        elif tag == "a" and self._a is not None:
            link_type, href = self._a_attrs
            self.links.append((self._navs[-1], link_type, href, " ".join("".join(self._a).split())))
            self._a = None

    def handle_data(self, data: str) -> None:
        if self._a is not None:
            self._a.append(data)


def _xml(z: zipfile.ZipFile, name: str) -> ElementTree.Element:
    return ElementTree.fromstring(z.read(name))


def _text(z: zipfile.ZipFile, name: str) -> str:
    return z.read(name).decode("utf-8", errors="replace")


def read_epub(path: str | os.PathLike[str]) -> Book:
    """Read the EPUB at path. Raises KeyError / ElementTree.ParseError on a broken file."""
    with zipfile.ZipFile(path) as z:
        container = _xml(z, "META-INF/container.xml")
        rootfile = container.find(".//{*}rootfile")
        if rootfile is None or not rootfile.get("full-path"):
            raise ValueError("EPUB container names no package document")
        opf_path = rootfile.get("full-path") or ""
        opf = _xml(z, opf_path)

        manifest: dict[str, tuple[str, str, str]] = {}  # id -> (path, media-type, properties)
        for item in opf.iterfind("{*}manifest/{*}item"):
            href = _resolve(opf_path, item.get("href", ""))[0]
            manifest[item.get("id", "")] = (href, item.get("media-type", ""), item.get("properties", ""))

        title_el = opf.find("{*}metadata/{*}title")
        author_el = opf.find("{*}metadata/{*}creator")
        title = " ".join((title_el.text or "").split()) if title_el is not None else ""
        author = " ".join((author_el.text or "").split()) if author_el is not None else ""

        spine_el = opf.find("{*}spine")
        spine: list[str] = []
        if spine_el is not None:
            for ref in spine_el.iterfind("{*}itemref"):
                entry = manifest.get(ref.get("idref", ""))
                if entry and entry[1] in _XHTML_TYPES:
                    spine.append(entry[0])

        cover_files: set[str] = set()
        contents_files: set[str] = set()
        for ref in opf.iterfind("{*}guide/{*}reference"):
            target = _resolve(opf_path, ref.get("href", ""))[0]
            kind = (ref.get("type") or "").lower()
            if kind == "cover":
                cover_files.add(target)
            elif kind == "toc":
                contents_files.add(target)

        cover_images = {p for p, _, props in manifest.values() if "cover-image" in props.split()}
        for meta in opf.iterfind("{*}metadata/{*}meta"):
            if meta.get("name") == "cover" and meta.get("content") in manifest:
                cover_images.add(manifest[meta.get("content", "")][0])

        toc: list[TocEntry] = []
        ncx_id = spine_el.get("toc") if spine_el is not None else None
        ncx_path = manifest[ncx_id][0] if ncx_id in manifest else next(
            (p for p, mt, _ in manifest.values() if mt == "application/x-dtbncx+xml"), None
        )
        if ncx_path and ncx_path in z.namelist():
            for point in _xml(z, ncx_path).iter("{http://www.daisy.org/z3986/2005/ncx/}navPoint"):
                label_el = point.find("{*}navLabel/{*}text")
                content = point.find("{*}content")
                if content is None or not content.get("src"):
                    continue
                label = " ".join((label_el.text or "").split()) if label_el is not None else ""
                file, fragment = _resolve(ncx_path, content.get("src", ""))
                toc.append(TocEntry(label, file, fragment))

        nav_path = next((p for p, _, props in manifest.values() if "nav" in props.split()), None)
        if nav_path and nav_path in z.namelist():
            contents_files.add(nav_path)
            nav = _NavParser()
            nav.feed(_text(z, nav_path))
            nav.close()
            for nav_type, link_type, href, text in nav.links:
                file, fragment = _resolve(nav_path, href)
                if "toc" in nav_type.split() and not ncx_path:
                    toc.append(TocEntry(text, file, fragment))
                elif "landmarks" in nav_type.split():
                    if "cover" in link_type.split():
                        cover_files.add(file)
                    elif "toc" in link_type.split():
                        contents_files.add(file)

        blocks: dict[str, list[Block]] = {}
        for file in spine:
            if file not in z.namelist():
                continue
            parser = _BlockParser(file)
            parser.feed(_text(z, file))
            parser.close()
            blocks[file] = parser.blocks
            if cover_images & parser.images:
                cover_files.add(file)

    return Book(
        tuple(spine), tuple(toc), frozenset(cover_files), frozenset(contents_files), blocks, title, author
    )
