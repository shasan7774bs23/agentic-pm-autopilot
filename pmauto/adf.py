"""Markdown (the subset the analyses use) to Atlassian Document Format, so a posted
comment shows real headings, lists and tables instead of raw '##' and '|' characters.

Supported: # headings, paragraphs, - / * bullets, 1. numbered items, | tables |,
fenced code blocks, **bold**, `code`. The machine-readable tail of an analysis
(<!-- ... --> and the FILES:/VERDICT:/QUESTIONS: lines) is never posted.
"""
import re

_INLINE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`)")
_MACHINE = re.compile(r"^(FILES|VERDICT|QUESTIONS):", re.I)


def strip_machine_block(md):
    out = []
    for line in md.splitlines():
        if line.strip().startswith("<!--") or _MACHINE.match(line.strip()):
            break
        out.append(line)
    return "\n".join(out).rstrip() + "\n"


def _inline(text):
    nodes = []
    for part in _INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            nodes.append({"type": "text", "text": part[2:-2], "marks": [{"type": "strong"}]})
        elif part.startswith("`") and part.endswith("`"):
            nodes.append({"type": "text", "text": part[1:-1], "marks": [{"type": "code"}]})
        else:
            nodes.append({"type": "text", "text": part})
    return nodes or [{"type": "text", "text": " "}]


def _para(text):
    return {"type": "paragraph", "content": _inline(text)}


def _cells(row):
    return [c.strip() for c in row.strip().strip("|").split("|")]


def to_adf(md):
    lines = md.splitlines()
    content, i = [], 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue
        if s.startswith("```"):
            i += 1
            code = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            content.append({"type": "codeBlock", "content": [{"type": "text", "text": "\n".join(code) or " "}]})
            continue
        m = re.match(r"^(#{1,6})\s+(.*)", s)
        if m:
            content.append({"type": "heading", "attrs": {"level": min(len(m.group(1)) + 1, 6)},
                            "content": _inline(m.group(2))})
            i += 1
            continue
        if s.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                if not re.match(r"^\|[\s:|-]+\|$", lines[i].strip()):
                    rows.append(_cells(lines[i]))
                i += 1
            table_rows = []
            for n, row in enumerate(rows):
                kind = "tableHeader" if n == 0 else "tableCell"
                table_rows.append({"type": "tableRow", "content": [
                    {"type": kind, "content": [_para(c or " ")]} for c in row]})
            content.append({"type": "table", "content": table_rows})
            continue
        bullet = re.match(r"^[-*]\s+(.*)", s)
        number = re.match(r"^\d+[.)]\s+(.*)", s)
        if bullet or number:
            kind = "bulletList" if bullet else "orderedList"
            rx = r"^[-*]\s+(.*)" if bullet else r"^\d+[.)]\s+(.*)"
            items = []
            while i < len(lines) and re.match(rx, lines[i].strip()):
                items.append({"type": "listItem", "content": [_para(re.match(rx, lines[i].strip()).group(1))]})
                i += 1
            content.append({"type": kind, "content": items})
            continue
        para = [s]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#|\||[-*]\s|\d+[.)]\s|```)", lines[i].strip()):
            para.append(lines[i].strip())
            i += 1
        content.append(_para(" ".join(para)))
    return {"type": "doc", "version": 1, "content": content}
