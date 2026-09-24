#!/usr/bin/env python3
"""Render EmComm_Field_Node_Whitepaper.md to PDF, beside it in the repo root.

    python3 build_whitepaper.py

The markdown is the source; the PDF is a build artifact that happens to be
committed, because it is what gets handed to somebody who is not going to
clone a repository to read a document.

A small renderer over a fixed subset -- headings, paragraphs, lists, code
blocks, tables -- rather than a markdown library. A subset parsed here is a
subset that cannot surprise the build.
"""
import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, ListFlowable,
                                ListItem, PageTemplate, Paragraph, Preformatted,
                                Spacer, Table, TableStyle)

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "EmComm_Field_Node_Whitepaper.md"
OUTPUT = HERE / "EmComm_Field_Node_Whitepaper.pdf"

INK = colors.HexColor("#14304a")
RULE = colors.HexColor("#c9c9c9")
MUTED = colors.HexColor("#555555")

BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=15.5,
                      alignment=TA_LEFT, spaceAfter=9)
H1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=23, leading=27,
                    textColor=colors.black, spaceAfter=3)
SUB = ParagraphStyle("sub", parent=BODY, fontSize=10.5, textColor=MUTED, spaceAfter=12)
H2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=14, leading=18,
                    textColor=INK, spaceBefore=4, spaceAfter=9)
H3 = ParagraphStyle("h3", fontName="Helvetica-Bold", fontSize=10.5, leading=14,
                    textColor=colors.HexColor("#333333"), spaceBefore=8, spaceAfter=5)
CODE = ParagraphStyle("code", fontName="Courier", fontSize=8.5, leading=12)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=9, leading=12.5, spaceAfter=0)
CELL_H = ParagraphStyle("cellh", parent=CELL, fontName="Helvetica-Bold", textColor=INK)


def inline(text: str) -> str:
    """Markdown inline markup to reportlab's mini-HTML.

    Applied to a whole block, not line by line: a bold span wrapping across a
    source newline is one span, and per-line handling emits a literal asterisk
    at the break.
    """
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"`([^`]+)`",
                  r'<font face="Courier" size="9" color="#10293f">\1</font>', text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.S)
    text = re.sub(r"(?<![*\w])\*([^*].*?)\*(?!\w)", r"<i>\1</i>", text, flags=re.S)
    return text


def bar(flowables, color, pad=7):
    t = Table([[flowables]], colWidths=[6.3 * inch])
    t.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), pad), ("RIGHTPADDING", (0, 0), (-1, -1), pad),
        ("TOPPADDING", (0, 0), (-1, -1), pad), ("BOTTOMPADDING", (0, 0), (-1, -1), pad),
        ("LINEBEFORE", (0, 0), (0, -1), 2.5, color), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return t


def code_block(lines):
    t = Table([[Preformatted("\n".join(lines), CODE)]], colWidths=[6.4 * inch])
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, RULE),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fbfbfb")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    return t


def table_block(rows):
    ncols = max(len(r) for r in rows)
    data = [[Paragraph(inline(c), CELL_H if i == 0 else CELL)
             for c in row + [""] * (ncols - len(row))]
            for i, row in enumerate(rows)]
    width = 6.4 * inch
    # The first column of a two-column table is a label; give it less room.
    widths = ([width * 0.34, width * 0.66] if ncols == 2
              else [width / ncols] * ncols)
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, RULE),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef3f7")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return t


def parse(md: str):
    blocks, lines, i = [], md.splitlines(), 0
    para = []
    lst = {"kind": None, "items": []}

    def flush():
        if para:
            blocks.append(("p", "\n".join(para))); para.clear()
        if lst["items"]:
            blocks.append((lst["kind"], list(lst["items"])))
        lst["kind"], lst["items"] = None, []

    def start(kind, text):
        if para:
            blocks.append(("p", "\n".join(para))); para.clear()
        if lst["kind"] not in (None, kind):
            blocks.append((lst["kind"], list(lst["items"]))); lst["items"] = []
        lst["kind"] = kind
        lst["items"].append(text)

    while i < len(lines):
        line = lines[i]
        if line.startswith("# "):
            flush(); blocks.append(("title", line[2:].strip()))
        elif line.startswith("%subtitle "):
            flush(); blocks.append(("subtitle", line[len("%subtitle "):].strip()))
        elif line.startswith("## "):
            flush(); blocks.append(("h2", line[3:].strip()))
        elif line.startswith("### "):
            flush(); blocks.append(("h3", line[4:].strip()))
        elif line.startswith("```"):
            flush(); body = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                body.append(lines[i]); i += 1
            blocks.append(("code", body))
        elif line.startswith("|"):
            flush(); rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(set(c) <= set("-: ") and c for c in cells):
                    rows.append(cells)
                i += 1
            i -= 1
            blocks.append(("table", rows))
        elif re.match(r"^\s*[-*] ", line):
            start("ul", re.sub(r"^\s*[-*] ", "", line))
        elif re.match(r"^\s*\d+\. ", line):
            start("ol", re.sub(r"^\s*\d+\. ", "", line))
        elif not line.strip():
            flush()
        else:
            if lst["items"]:
                lst["items"][-1] += " " + line.strip()
            else:
                para.append(line.strip())
        i += 1
    flush()
    return blocks


def build(blocks):
    story = []
    for kind, payload in blocks:
        if kind == "title":
            story.append(Paragraph(inline(payload), H1))
        elif kind == "subtitle":
            story.append(Paragraph(inline(payload), SUB))
            r = Table([[""]], colWidths=[6.5 * inch], rowHeights=[0.5])
            r.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 1.2, INK)]))
            story += [r, Spacer(1, 16)]
        elif kind == "h2":
            story.append(KeepTogether([Spacer(1, 6),
                                       bar([Paragraph(inline(payload), H2)], INK, pad=6)]))
        elif kind == "h3":
            story.append(Paragraph(inline(payload), H3))
        elif kind == "code":
            story += [code_block(payload), Spacer(1, 10)]
        elif kind == "table":
            story += [table_block(payload), Spacer(1, 10)]
        elif kind == "p":
            story.append(Paragraph(inline(payload), BODY))
        elif kind in ("ul", "ol"):
            items = [ListItem(Paragraph(inline(t), BODY), leftIndent=18) for t in payload]
            story.append(ListFlowable(
                items, bulletType="bullet" if kind == "ul" else "1",
                start=None if kind == "ol" else None,
                bulletFormat=None if kind == "ul" else "%s.",
                leftIndent=18, bulletFontSize=9))
            story.append(Spacer(1, 6))
    return story


def footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(RULE); canvas.setLineWidth(0.6)
    canvas.line(1 * inch, 0.78 * inch, 7.5 * inch, 0.78 * inch)
    canvas.setFont("Helvetica", 8); canvas.setFillColor(MUTED)
    canvas.drawString(1 * inch, 0.6 * inch,
                      "EmComm Field Node — Provisioner white paper")
    canvas.drawRightString(7.5 * inch, 0.6 * inch, str(doc.page))
    canvas.restoreState()


def main():
    md = SOURCE.read_text()
    sub = next(l[len("%subtitle "):].strip()
               for l in md.splitlines() if l.startswith("%subtitle "))
    doc = BaseDocTemplate(
        str(OUTPUT), pagesize=letter,
        leftMargin=1 * inch, rightMargin=1 * inch,
        topMargin=0.9 * inch, bottomMargin=1 * inch,
        title="EmComm Field Node — Provisioner white paper",
        author="EmComm Field Node", subject=sub)
    doc.addPageTemplates([PageTemplate(
        id="p",
        frames=[Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")],
        onPage=footer)])
    doc.build(build(parse(md)))
    print("built %s (%.0f KB)" % (OUTPUT.name, OUTPUT.stat().st_size / 1024))


if __name__ == "__main__":
    sys.exit(main())
