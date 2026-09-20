#!/usr/bin/env python3
"""Render the operator's manual to a printable PDF.

The PDF is a BUILD ARTIFACT, not a tracked document. docs/*.pdf is gitignored,
and the markdown is the single source of truth -- a hand-maintained PDF is a
second copy of facts that already live in the code, and this project has been
bitten by two-copies drift more than once.

    python3 scripts/build_manual.py

Output lands in docs/, which is also where the provisioner's document-server
step picks PDFs up from, so a built manual is copied onto every node it
provisions and served by the node's own reference library.

Document metadata is set explicitly rather than left to defaults: a PDF embeds
author and producer fields, and the manual's own figure rules say not to ship
operator identity. That applies to the container as much as the content.
"""
import html
import re
import sys
from pathlib import Path

from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import (BaseDocTemplate, Frame, PageBreak, PageTemplate,
                                Paragraph, Preformatted, Spacer, Table, TableStyle,
                                HRFlowable)

SRC = Path("docs/OPERATORS_MANUAL.md")
OUT = Path("docs/EmComm-Operators-Manual.pdf")

TITLE = "EmComm Field Node - Operator's Manual"
# Deliberately impersonal. No operator name, no callsign, no hostname.
AUTHOR = "EmComm Field Node project"


# --- inline markdown -> reportlab's mini-XML -------------------------------
def inline(text):
    """**bold**, `code` and links, with everything else escaped.

    reportlab accepts a small XML subset; anything unescaped that looks like a
    tag will either vanish or abort the build, so escape first and add markup
    after.

    Code spans go first so that a `*` inside one -- alsa_output.* and plughw:
    are the manual's regular offenders -- is already wrapped before the bold
    pass sees it. The bold pattern is non-greedy rather than "no asterisks",
    because the manual bolds phrases that contain code spans with asterisks
    in them, and [^*]+ silently declines to match those.
    """
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`",
                  r'<font face="Courier" size="9">\1</font>', text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    # [label](url) -> label, then the bare url. A printed manual cannot be
    # clicked, so the address has to survive as text.
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    return text


def build_styles():
    ss = getSampleStyleSheet()
    body = ParagraphStyle("Body", parent=ss["BodyText"], fontSize=9.5,
                          leading=13.5, spaceAfter=7, alignment=TA_LEFT)
    return {
        "h1": ParagraphStyle("H1", parent=ss["Heading1"], fontSize=17, leading=21,
                             spaceBefore=0, spaceAfter=12,
                             textColor=colors.HexColor("#1a1a1a")),
        "h2": ParagraphStyle("H2", parent=ss["Heading2"], fontSize=13.5, leading=17,
                             spaceBefore=16, spaceAfter=7,
                             textColor=colors.HexColor("#1a1a1a")),
        # Chapters open a page, so the leading gap above them is dead space.
        "h2_top": ParagraphStyle("H2Top", parent=ss["Heading2"], fontSize=13.5,
                                 leading=17, spaceBefore=0, spaceAfter=9,
                                 textColor=colors.HexColor("#1a1a1a")),
        "h3": ParagraphStyle("H3", parent=ss["Heading3"], fontSize=11, leading=14,
                             spaceBefore=12, spaceAfter=5,
                             textColor=colors.HexColor("#333333")),
        "body": body,
        "bullet": ParagraphStyle("Bullet", parent=body, leftIndent=16,
                                 bulletIndent=5, spaceAfter=4),
        "quote": ParagraphStyle("Quote", parent=body, leftIndent=14,
                                rightIndent=10, borderPadding=5,
                                backColor=colors.HexColor("#f4f4f2"),
                                spaceBefore=6, spaceAfter=8),
        "code": ParagraphStyle("Code", parent=ss["Code"], fontSize=8.3,
                               leading=10.6, leftIndent=14,
                               textColor=colors.HexColor("#202020")),
        "cell": ParagraphStyle("Cell", parent=body, fontSize=8.6, leading=11,
                               spaceAfter=0),
        "cellh": ParagraphStyle("CellH", parent=body, fontSize=8.6, leading=11,
                                spaceAfter=0, fontName="Helvetica-Bold"),
    }


def flush_table(rows, st, story):
    """A markdown pipe table -> a reportlab Table, header row bolded."""
    if not rows:
        return
    # Drop the |---|---| separator row.
    rows = [r for r in rows if not re.match(r"^[\s|:-]+$", r)]
    if not rows:
        return
    data, ncols = [], 0
    for i, raw in enumerate(rows):
        cells = [c.strip() for c in raw.strip().strip("|").split("|")]
        style = st["cellh"] if i == 0 else st["cell"]
        data.append([Paragraph(inline(c), style) for c in cells])
        ncols = max(ncols, len(cells))
    for row in data:                       # ragged rows would raise
        while len(row) < ncols:
            row.append(Paragraph("", st["cell"]))
    avail = 6.5 * inch
    # repeatRows: a table that splits across a page break otherwise continues
    # with unlabelled columns, which for the failure tables means a page of
    # causes with no symptoms beside them.
    t = Table(data, colWidths=[avail / ncols] * ncols, hAlign="LEFT",
              repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ececea")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b8b8b4")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(t)
    story.append(Spacer(1, 9))


def starts_block(raw):
    """True if this raw line opens a new block rather than continuing one.

    The manual is hard-wrapped, so a paragraph, a bullet or a numbered item
    routinely spans several source lines. Rendering one Paragraph per source
    line breaks any span that crosses a line end -- **bold** written across a
    wrap came out as literal asterisks in the PDF -- and puts a paragraph gap
    between the halves of a sentence. Lines are gathered until one of these
    says a new block has begun.
    """
    s = raw.strip()
    if not s:
        return True
    if raw.startswith("    "):             # indented code, handled upstream
        return True
    if s.startswith(("```", "|", ">", "#")):
        return True
    if s == "---":
        return True
    return bool(re.match(r"^[*-] ", s) or re.match(r"^\d+\. ", s))


def gather(lines, i):
    """Consume a wrapped block from line i. Returns (joined text, next index).

    i must already point at the block's first line; its leading marker (bullet
    or number) is the caller's to strip.
    """
    parts, i = [lines[i].strip()], i + 1
    while i < len(lines) and not starts_block(lines[i]):
        parts.append(lines[i].strip())
        i += 1
    return " ".join(parts), i


def next_is_heading(lines, i):
    """True if the next non-blank line is a part or chapter heading.

    Used to drop the rule that used to separate sections: with every section
    starting its own page, a horizontal rule at the foot of the previous one
    is just a smudge above white space.
    """
    for j in range(i + 1, len(lines)):
        t = lines[j].strip()
        if not t:
            continue
        return bool(re.match(r"^#{1,2} ", t))
    return False


def render(md, st):
    story, lines = [], md.splitlines()
    i, table, code = 0, [], []
    while i < len(lines):
        ln = lines[i]

        # fenced code
        if ln.strip().startswith("```"):
            i += 1
            code = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i]); i += 1
            i += 1
            if code:
                story.append(Preformatted("\n".join(code), st["code"]))
                story.append(Spacer(1, 7))
            continue

        # pipe table
        if ln.strip().startswith("|"):
            table.append(ln); i += 1
            continue
        if table:
            flush_table(table, st, story); table = []

        # indented code (4 spaces), the manual's usual form
        if ln.startswith("    ") and ln.strip():
            code = []
            while i < len(lines) and (lines[i].startswith("    ") or not lines[i].strip()):
                if lines[i].strip() or (code and any(
                        j < len(lines) and lines[j].startswith("    ")
                        for j in range(i + 1, min(i + 3, len(lines))))):
                    code.append(lines[i][4:])
                i += 1
            while code and not code[-1].strip():
                code.pop()
            if code:
                story.append(Preformatted("\n".join(code), st["code"]))
                story.append(Spacer(1, 7))
            continue

        s = ln.strip()
        if not s:
            i += 1; continue

        if s == "---":
            if not next_is_heading(lines, i):
                story.append(Spacer(1, 5))
                story.append(HRFlowable(width="100%", thickness=0.6,
                                        color=colors.HexColor("#c0c0bc")))
                story.append(Spacer(1, 7))
        elif s.startswith("### "):
            # Subsections stay with their chapter -- breaking here would
            # scatter a chapter across pages that are mostly white.
            story.append(Paragraph(inline(s[4:]), st["h3"]))
        elif s.startswith("## "):
            if story:
                story.append(PageBreak())
            story.append(Paragraph(inline(s[3:]), st["h2_top"]))
        elif s.startswith("# "):
            if story:
                story.append(PageBreak())
            story.append(Paragraph(inline(s[2:]), st["h1"]))
        elif s.startswith("> "):
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip(">").strip()); i += 1
            story.append(Paragraph(inline(" ".join(q for q in quote if q)), st["quote"]))
            continue
        elif re.match(r"^[*-] ", s):
            text, i = gather(lines, i)
            story.append(Paragraph(inline(text[2:]), st["bullet"], bulletText="•"))
            continue
        elif re.match(r"^\d+\. ", s):
            text, i = gather(lines, i)
            n, rest = text.split(". ", 1)
            story.append(Paragraph(inline(rest), st["bullet"], bulletText=n + "."))
            continue
        else:
            text, i = gather(lines, i)
            story.append(Paragraph(inline(text), st["body"]))
            continue
        i += 1

    if table:
        flush_table(table, st, story)
    return story


def main():
    if not SRC.is_file():
        sys.exit("source not found: %s (run from the repository root)" % SRC)
    st = build_styles()
    story = render(SRC.read_text(encoding="utf-8"), st)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(str(OUT), pagesize=LETTER,
                          leftMargin=0.9 * inch, rightMargin=0.9 * inch,
                          topMargin=0.85 * inch, bottomMargin=0.8 * inch,
                          title=TITLE, author=AUTHOR, subject=TITLE,
                          creator=AUTHOR)

    def furniture(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#6a6a66"))
        canvas.drawString(0.9 * inch, 0.5 * inch, TITLE)
        canvas.drawRightString(LETTER[0] - 0.9 * inch, 0.5 * inch,
                               "page %d" % canvas.getPageNumber())
        canvas.restoreState()

    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height,
                  id="body")
    doc.addPageTemplates([PageTemplate(id="all", frames=[frame],
                                       onPage=furniture)])
    # build() consumes the list it is given, so count first.
    n = len(story)
    doc.build(story)
    print("wrote %s (%d flowables, %.0f KB)"
          % (OUT, n, OUT.stat().st_size / 1024))


if __name__ == "__main__":
    main()
