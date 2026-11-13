"""One document model, rendered to Markdown (GitHub issue) and self-contained HTML (email, file)."""
import html
from dataclasses import dataclass, field


@dataclass
class Link:
    text: str
    url: str


@dataclass
class Section:
    title: str
    note: str = ""
    headers: list = None
    rows: list = None
    bullets: list = None
    empty: str = "Nothing to report."


@dataclass
class Doc:
    title: str
    subtitle: str = ""
    sections: list = field(default_factory=list)


def _md_cell(c):
    if isinstance(c, Link):
        return f"[{c.text}]({c.url})"
    return str(c if c is not None else "").replace("|", "\\|").replace("\n", " ")


def to_markdown(doc):
    out = [f"# {doc.title}", ""]
    if doc.subtitle:
        out += [doc.subtitle, ""]
    for s in doc.sections:
        out += [f"## {s.title}", ""]
        if s.note:
            out += [s.note, ""]
        if s.headers is not None:
            if s.rows:
                out.append("| " + " | ".join(s.headers) + " |")
                out.append("|" + "---|" * len(s.headers))
                out += ["| " + " | ".join(_md_cell(c) for c in r) + " |" for r in s.rows]
            else:
                out.append(f"_{s.empty}_")
            out.append("")
        if s.bullets:
            out += [f"- {_md_cell(b)}" for b in s.bullets] + [""]
    return "\n".join(out)


def _h_cell(c):
    if isinstance(c, Link):
        return f'<a href="{html.escape(c.url)}">{html.escape(c.text)}</a>'
    return html.escape(str(c if c is not None else "")).replace("\n", "<br>")


CSS = """
:root{--bg:#fbfbfa;--fg:#1d1d1f;--mut:#5b5b62;--bd:#dcdcdf;--th:#eef1f6;--acc:#2d5fb3}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#16171a;--fg:#e8e8ea;--mut:#a3a3ab;--bd:#34353b;--th:#22262e;--acc:#7ea6ec}}
:root[data-theme="dark"]{--bg:#16171a;--fg:#e8e8ea;--mut:#a3a3ab;--bd:#34353b;--th:#22262e;--acc:#7ea6ec}
html,body{margin:0;padding:0;height:100%}
body{overflow:hidden;background:var(--bg);color:var(--fg);font:14px/1.5 "Segoe UI",system-ui,sans-serif}
.scroll-root{height:100vh;height:100dvh;overflow-y:auto;-webkit-overflow-scrolling:touch;outline:none}
main{max-width:1100px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:22px;margin:0 0 4px} .sub{color:var(--mut);margin:0 0 18px}
h2{font-size:17px;margin:26px 0 8px;border-bottom:1px solid var(--bd);padding-bottom:4px}
.tw{overflow-x:auto} table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border:1px solid var(--bd);padding:5px 8px;text-align:left;vertical-align:top} th{background:var(--th)}
a{color:var(--acc)} .note{color:var(--mut)} .empty{color:var(--mut);font-style:italic}
"""

SCRIPT = """<script>(function(){var s=document.querySelector('.scroll-root');if(!s)return;
function f(){try{s.focus({preventScroll:true})}catch(e){s.focus()}}
window.addEventListener('load',f);document.addEventListener('pointerdown',function(){setTimeout(f,0)});
window.addEventListener('keydown',function(e){if(document.activeElement===s)return;var h=s.clientHeight,
m={PageDown:h*.9,PageUp:-h*.9,ArrowDown:40,ArrowUp:-40,' ':e.shiftKey?-h*.9:h*.9};
if(e.key in m){s.scrollBy(0,m[e.key]);e.preventDefault()}else if(e.key==='Home'){s.scrollTo(0,0);e.preventDefault()}
else if(e.key==='End'){s.scrollTo(0,s.scrollHeight);e.preventDefault()}});
window.addEventListener('wheel',function(e){if(!s.contains(e.target))s.scrollBy(0,e.deltaY)},{passive:true});})();</script>"""


def _sections_html(doc):
    parts = [f"<h1>{html.escape(doc.title)}</h1>"]
    if doc.subtitle:
        parts.append(f'<p class="sub">{html.escape(doc.subtitle)}</p>')
    for s in doc.sections:
        parts.append(f"<h2>{html.escape(s.title)}</h2>")
        if s.note:
            parts.append(f'<p class="note">{html.escape(s.note)}</p>')
        if s.headers is not None:
            if s.rows:
                head = "".join(f"<th>{html.escape(h)}</th>" for h in s.headers)
                body = "".join("<tr>" + "".join(f"<td>{_h_cell(c)}</td>" for c in r) + "</tr>" for r in s.rows)
                parts.append(f'<div class="tw"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>')
            else:
                parts.append(f'<p class="empty">{html.escape(s.empty)}</p>')
        if s.bullets:
            parts.append("<ul>" + "".join(f"<li>{_h_cell(b)}</li>" for b in s.bullets) + "</ul>")
    return "".join(parts)


def to_html(doc):
    """Standalone page: scrolls inside an inner viewport box so it works in any viewer."""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{html.escape(doc.title)}</title><style>{CSS}</style></head>"
            f'<body><div class="scroll-root" tabindex="-1"><main>{_sections_html(doc)}</main></div>{SCRIPT}</body></html>')


def to_email_html(doc):
    """Email clients ignore most CSS; use plain inline styling and normal page flow."""
    td = 'style="border:1px solid #ccc;padding:4px 7px;vertical-align:top;font-size:13px"'
    body = _sections_html(doc).replace("<td>", f"<td {td}>").replace("<th>", f'<th {td[:-1]};background:#eef1f6">')
    body = body.replace("<table>", '<table style="border-collapse:collapse">')
    return f'<html><body style="font-family:Segoe UI,Arial,sans-serif;font-size:14px">{body}</body></html>'
