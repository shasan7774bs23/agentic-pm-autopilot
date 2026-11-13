"""Markdown (the comment drafts' subset) -> Jira wiki markup, so a comment can show attached images inline."""
import os
import re


def _inline(s):
    s = re.sub(r"\*\*(.+?)\*\*", r"*\1*", s)
    return s


def to_wiki(md, images=(), after_heading="Where"):
    """images are attached file paths; they are shown right after the section named after_heading
    (or at the end if that section is missing)."""
    out, placed, pending = [], False, False
    tags = [f"!{os.path.basename(p)}|width=900!" for p in images]
    for line in md.splitlines():
        s = line.rstrip()
        m = re.match(r"^(#{1,6})\s+(.*)", s)
        if m:
            if pending and tags:
                out += ["", *tags, ""]; placed, pending = True, False
            level = min(len(m.group(1)) + 1, 6)
            out.append(f"h{level}. {_inline(m.group(2))}")
            pending = m.group(2).strip().lower().startswith(after_heading.lower())
            continue
        n = re.match(r"^\s*\d+\.\s+(.*)", s)
        b = re.match(r"^\s*[-*]\s+(.*)", s)
        if n:
            out.append(f"# {_inline(n.group(1))}")
        elif b:
            out.append(f"* {_inline(b.group(1))}")
        else:
            out.append(_inline(s))
    if tags and not placed:
        out += ["", *tags]
    return "\n".join(out).strip() + "\n"
