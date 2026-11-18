"""Synthetic EPUBs for the split tests. Every word is made-up filler; no book text."""

from __future__ import annotations

import zipfile
from collections.abc import Sequence
from pathlib import Path

_VOCAB = "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor".split()


def prose(marker: str, n: int = 20) -> str:
    return " ".join([marker] + [_VOCAB[i % len(_VOCAB)] for i in range(n - 1)]) + "."


def p(cls: str, text: str) -> str:
    return f'<p class="{cls}">{text}</p>'


def _xhtml(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">\n'
        f"<head><title>t</title><style>p {{ margin: 0 }}</style></head>\n<body>{body}</body>\n</html>\n"
    )


WATERMARK = '<div><p><a href="https://example.com"><i>Example.com</i></a></p></div>'


def write_epub(
    path: Path,
    files: list[tuple[str, str]],
    *,
    toc: Sequence[tuple[str, str]] = (),
    nav: bool = False,
    landmarks: Sequence[tuple[str, str]] = (),
    guide: Sequence[tuple[str, str]] = (),
    images: Sequence[str] = (),
    cover_image: str | None = None,
    epub2_cover_meta: bool = False,
    title: str = "T",
    authors: Sequence[str] = (),
) -> Path:
    """files: (name, body html) in spine order; toc: (label, href). nav=True: EPUB3 nav, no NCX."""
    items, spine = [], []
    for i, (name, _) in enumerate(files):
        items.append(f'<item id="f{i}" href="{name}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="f{i}"/>')
    for i, name in enumerate(images):
        props = ' properties="cover-image"' if name == cover_image and not epub2_cover_meta else ""
        items.append(f'<item id="img{i}" href="{name}" media-type="image/jpeg"{props}/>')
    meta = ""
    if epub2_cover_meta and cover_image:
        meta = f'<meta name="cover" content="img{list(images).index(cover_image)}"/>'
    if nav:
        items.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
    else:
        items.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
    creators = "".join(f"<dc:creator>{a}</dc:creator>" for a in authors)
    guide_xml = "".join(f'<reference type="{t}" href="{h}" title="{t}"/>' for t, h in guide)
    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:title>{title}</dc:title>{creators}'
        f'<dc:identifier id="id">x</dc:identifier>{meta}</metadata>'
        f'<manifest>{"".join(items)}</manifest>'
        f'<spine{"" if nav else " toc=" + chr(34) + "ncx" + chr(34)}>{"".join(spine)}</spine>'
        f"{'<guide>' + guide_xml + '</guide>' if guide else ''}</package>"
    )
    container = (
        '<?xml version="1.0"?><container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
        '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
        "</rootfiles></container>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        for name, body in files:
            z.writestr(f"OEBPS/{name}", _xhtml(body))
        for name in images:
            z.writestr(f"OEBPS/{name}", b"\xff\xd8\xff")
        if nav:
            toc_li = "".join(f'<li><a href="{h}">{label}</a></li>' for label, h in toc)
            marks = "".join(f'<li><a epub:type="{t}" href="{h}">{t}</a></li>' for t, h in landmarks)
            body = f'<nav epub:type="toc"><h2>Contents</h2><ol>{toc_li}</ol></nav>'
            if landmarks:
                body += f'<nav epub:type="landmarks"><ol>{marks}</ol></nav>'
            z.writestr("OEBPS/nav.xhtml", _xhtml(body))
        else:
            points = "".join(
                f'<navPoint id="n{i}" playOrder="{i + 1}"><navLabel><text>{label}</text></navLabel>'
                f'<content src="{h}"/></navPoint>'
                for i, (label, h) in enumerate(toc)
            )
            ncx = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
                f"<navMap>{points}</navMap></ncx>"
            )
            z.writestr("OEBPS/toc.ncx", ncx)
    return path


def toc_book(tmp_path: Path) -> Path:
    """Untitled file joining the unit before, part page inside the story, mid-file TOC anchor."""
    files = [
        ("cover.xhtml", '<figure epub:type="cover"><img src="img/cover.jpg" alt=""/></figure>' + WATERMARK),
        (
            "contents.xhtml",
            '<section epub:type="toc">'
            + p("Prelims", "Contents")
            + '<p class="TocLine"><a href="ch1.xhtml#c1">Chapter 1</a></p>'
            + '<p class="TocLine"><a href="part.xhtml#c2">Chapter 2</a></p>'
            + "</section>"
            + WATERMARK,
        ),
        ("legend.xhtml", p("Centred", prose("legendone")) + p("Centred", prose("legendtwo")) + WATERMARK),
        ("title.xhtml", '<div><img src="img/title.jpg" alt="A Title"/></div>' + WATERMARK),
        (
            "ack.xhtml",
            '<section epub:type="acknowledgments">' + p("Special", prose("ackone", 12)) + "</section>" + WATERMARK,
        ),
        (
            "int-a.xhtml",
            '<section epub:type="part"><p class="Hidden"> </p>'
            '<h1 id="i1" class="Part"><a id="anchor-i1"></a>The Quiet</h1>'
            + p("QuoteTop", "Walk the long road slow and the road walks you")
            + "".join(p("Quote", f"verse line {n} of the long walk") for n in range(2, 8))
            + p("QuoteAttr", "– from the <i>Book of Small Hours</i>")
            + "</section>"
            + WATERMARK,
        ),
        (
            "int-b.xhtml",
            '<p class="Blank"> <br/></p>'
            + p("NoIndent", prose("intone"))
            + p("Body", prose("inttwo"))
            + p("Body", prose("intthree"))
            + WATERMARK,
        ),
        (
            "ch1.xhtml",
            '<h1 id="c1">CHAPTER 1</h1><p class="Icon"><img src="img/line.jpg" alt=""/></p>'
            + p("Subtitle", "On Small Things")
            + p("NoIndent", prose("c1sceneone"))
            + p("Body", prose("c1b"))
            + p("Body", prose("c1c"))
            + p("NoIndent", prose("c1scenetwo"))
            + p("Body", prose("c1d"))
            + WATERMARK,
        ),
        (
            "part.xhtml",
            '<h1 class="Part">Part Two</h1><p class="PartIcon"><img src="img/line.jpg" alt=""/></p>'
            '<h1 id="c2">Chapter 2</h1>'
            + p("NoIndent", prose("c2sceneone"))
            + p("Body", prose("c2b"))
            + p("Body", "He paused – then he went on and on")
            + WATERMARK,
        ),
        (
            "afterword.xhtml",
            '<section epub:type="afterword"><h1>Afterword</h1>'
            + p("Body", prose("aftone"))
            + p("Body", prose("afttwo"))
            + "</section>"
            + WATERMARK,
        ),
        ("about.xhtml", p("Special", prose("aboutone")) + WATERMARK),
    ]
    toc = [
        ("Contents", "contents.xhtml"),
        ("Legend", "legend.xhtml"),
        ("A Title", "title.xhtml"),
        ("Acknowledgments", "ack.xhtml"),
        ("The Quiet", "int-a.xhtml#anchor-i1"),
        ("Chapter 1", "ch1.xhtml#c1"),
        ("Part Two", "part.xhtml"),
        ("Chapter 2", "part.xhtml#c2"),
        ("Afterword", "afterword.xhtml"),
        ("About the Author", "about.xhtml"),
    ]
    return write_epub(
        tmp_path / "toc.epub",
        files,
        toc=toc,
        guide=[("cover", "cover.xhtml"), ("toc", "contents.xhtml#top")],
        images=["img/cover.jpg", "img/title.jpg", "img/line.jpg"],
    )


def links_book(tmp_path: Path, cover_by: str = "image") -> Path:
    """No guide; the contents page is found by its internal links alone.
    cover_by: "image" (manifest cover-image) or "landmarks" (nav landmarks)."""
    files = [
        ("front.xhtml", '<div><img src="img/front.jpg" alt=""/></div>'),
        (
            "toc-page.xhtml",
            p("Head", "Contents")
            + '<p class="Line"><a href="c1.xhtml">One</a></p>'
            + '<p class="Line"><a href="c2.xhtml">Two</a></p>',
        ),
        ("c1.xhtml", "<h1>Chapter One</h1>" + p("Body", prose("oneone")) + p("Body", prose("onetwo"))),
        ("c2.xhtml", "<h1>Chapter Two</h1>" + p("Body", prose("twoone")) + p("Body", prose("twotwo"))),
    ]
    toc = [("Cover", "front.xhtml"), ("Contents", "toc-page.xhtml"), ("Chapter One", "c1.xhtml"), ("Chapter Two", "c2.xhtml")]
    return write_epub(
        tmp_path / f"links-{cover_by}.epub",
        files,
        toc=toc,
        nav=True,
        landmarks=[("cover", "front.xhtml")] if cover_by == "landmarks" else [],
        images=["img/front.jpg"],
        cover_image="img/front.jpg" if cover_by == "image" else None,
    )


def divider_book(tmp_path: Path, *, adjacent: bool = False, text_before_later_epigraph: bool = False) -> Path:
    """Divider sections crossing a file boundary, all in one paragraph class."""
    c = "calibre2"
    div = p(c, "==========")
    stray = p(c, prose("strayline")) if text_before_later_epigraph else ""
    f1 = (
        p(c, "Some Title Some Author")
        + p(c, "Copyright 2001")
        + p(c, "Keep the lamp lit and the door shut. -Sayings of the Old Guard")
        + p(c, "s1alpha begins -- and then it goes on -- for a while " + prose("x"))
        + p(c, prose("s1beta"))
        + div
        + (div if adjacent else "")
        + stray
        + p(c, "Feet on stone and stone on feet. -from a marching song")
        + p(c, prose("s2alpha"))
    )
    f2 = (
        p(c, prose("s2beta"))
        + div
        + p(c, "A verse line here and gone")
        + p(c, "-Book of Verses")
        + p(c, prose("s3alpha"))
        + div
        # a 14-word source, just inside MAX_SOURCE_WORDS
        + p(c, "Hold the line until the last lamp fails. "
               "-from the Collected Sayings of the Old Guard as told around the winter fires")
        + p(c, prose("s4alpha"))
        + div
        # not epigraphs: a dash before punctuation (dialogue) or a digit
        + p(c, "‘I only meant –’")
        + p(c, "The gauge on the wall read -5 degrees that morning.")
        + p(c, prose("s5alpha"))
    )
    files = [
        (
            "title.xhtml",
            '<div><svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
            '<image xlink:href="cover.jpeg"/></svg></div>',
        ),
        ("f1.html", f'<div class="calibre1">{f1}</div>'),
        ("f2.html", f2),
    ]
    return write_epub(
        tmp_path / "divider.epub",
        files,
        toc=[("Start", "title.xhtml")],
        guide=[("cover", "title.xhtml")],
        images=["cover.jpeg"],
        cover_image="cover.jpeg",
        epub2_cover_meta=True,
    )


def mostly_appendix_book(tmp_path: Path) -> Path:
    files = [
        ("c1.xhtml", "<h1>Chapter 1</h1>" + "".join(p("Body", prose(f"short{i}", 5)) for i in range(3))),
        ("app.xhtml", "<h1>Appendix</h1>" + p("Note", prose("noteone", 100)) + p("Note", prose("notetwo", 100))),
    ]
    return write_epub(
        tmp_path / "appendix.epub", files, toc=[("Chapter 1", "c1.xhtml"), ("Appendix", "app.xhtml")]
    )
