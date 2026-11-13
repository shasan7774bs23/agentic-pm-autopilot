"""Context pack for the Ticket Analyst: the ticket as it stood at a moment in time, plus pointers to the code.
Read-only. Used by the Claude Code skill in .claude/skills/ticket-analyst."""
import datetime as dt
import hashlib
import json
import os
import re

from .model import adf_text, client_ref, parse_ts

FIELDS = ["summary", "description", "issuetype", "priority", "status", "reporter", "assignee", "created",
          "comment", "attachment", "issuelinks", "project", "fixVersions"]


def description_as_of(current_text, histories, as_of):
    """Walk description edits made after `as_of` backwards, restoring the older text each time."""
    text = current_text
    edits = []
    for h in histories:
        when = parse_ts(h["created"])
        for item in h.get("items", []):
            if item.get("field") == "description" and when > as_of:
                edits.append((when, item.get("fromString") or ""))
    for _, old in sorted(edits, reverse=True):
        text = old
    return text, len(edits)


def content_hash(text, comments, cfg):
    client = [c for c in comments if c["author_id"] in cfg["client_accounts"]]
    blob = text + "\n".join(c["text"] for c in client)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def build(jira, key, cfg, as_of=None):
    raw = jira.get(f"/rest/api/3/issue/{key}", fields=",".join(FIELDS))
    f = raw["fields"]
    created = parse_ts(f["created"])
    as_of = as_of or dt.datetime.now(dt.timezone.utc)
    histories = jira.all_changelog(key)
    desc, edits_undone = description_as_of(adf_text(f.get("description")), histories, as_of)
    comments = []
    for c in jira.all_comments(key):
        when = parse_ts(c["created"])
        if when <= as_of:
            a = c.get("author") or {}
            comments.append({"author_id": a.get("accountId", ""), "author": a.get("displayName", "?"),
                             "created": when.isoformat(), "text": adf_text(c.get("body")).strip()})
    attachments = [{"filename": a["filename"], "url": a["content"], "size": a.get("size", 0)}
                   for a in f.get("attachment") or [] if parse_ts(a["created"]) <= as_of]
    links = []
    for l in f.get("issuelinks") or []:
        other = l.get("outwardIssue") or l.get("inwardIssue") or {}
        links.append(f"{l['type']['name']}: {other.get('key')} {other.get('fields', {}).get('summary', '')}")
    project = f["project"]["key"]
    code = cfg["code_repos"].get(project, {})
    reporter = (f.get("reporter") or {})
    return {
        "key": key, "url": f"{jira.base}/browse/{key}", "summary": f["summary"], "project": project,
        "client_ref": client_ref(f["summary"], cfg), "client_ref_label": (cfg.get("client_reference") or {}).get("label", "Client ref"),
        "type": f["issuetype"]["name"], "priority": (f.get("priority") or {}).get("name"),
        "reporter": cfg["client_accounts"].get(reporter.get("accountId"), reporter.get("displayName")),
        "reporter_is_client": reporter.get("accountId") in cfg["client_accounts"],
        "created": created.isoformat(), "as_of": as_of.isoformat(), "description_edits_undone": edits_undone,
        "description": desc.strip(), "comments": comments, "attachments": attachments, "links": links,
        "code": code, "content_hash": content_hash(desc, comments, cfg),
    }


def to_markdown(ctx):
    lines = [f"# {ctx['key']} — {ctx['summary']}", "",
             f"- Project: {ctx['project']} · Type: {ctx['type']} · Priority: {ctx['priority']}",
             f"- Raised by: {ctx['reporter']}{' (client)' if ctx['reporter_is_client'] else ''} · Created: {ctx['created'][:16]}"
             + (f" · {ctx.get('client_ref_label', 'Client ref')}: {ctx['client_ref']}" if ctx.get("client_ref") else ""),
             f"- Ticket text as of: {ctx['as_of'][:16]} (later edits removed: {ctx['description_edits_undone']})",
             f"- Code: {ctx['code'].get('repo', '?')} @ {ctx['code'].get('ref', ctx['code'].get('branch', '?'))}", "",
             "## Description", "", ctx["description"] or "(empty)", ""]
    lines += ["## Comments up to that time", ""]
    lines += [f"**{c['author']}** ({c['created'][:10]}): {c['text']}" for c in ctx["comments"]] or ["(none)"]
    lines += ["", "## Attachments", ""] + ([f"- {a['filename']}" + (f" → saved at `{a['local']}`" if a.get("local") else "")
                                          + (f" (text extracted: `{a['text']}`)" if a.get("text") else "")
                                          for a in ctx["attachments"]] or ["(none)"])
    lines += ["", "## Linked issues", ""] + ([f"- {l}" for l in ctx["links"]] or ["(none)"])
    return "\n".join(lines) + "\n"


def _docx_text(path):
    import zipfile
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"</w:p>", chr(10), xml)
    return re.sub(r"<[^>]+>", "", xml)


def fetch_attachments(jira, ctx, out_dir, max_bytes=15_000_000):
    folder = os.path.join(out_dir, "attachments", ctx["key"])
    for a in ctx["attachments"]:
        if a["size"] > max_bytes:
            continue
        os.makedirs(folder, exist_ok=True)
        dest = os.path.join(folder, re.sub(r'[<>:"/\|?*]', "_", a["filename"]))
        jira.download(a["url"], dest)
        a["local"] = dest
        if dest.lower().endswith(".docx"):
            txt = dest + ".txt"
            with open(txt, "w", encoding="utf-8") as fh:
                fh.write(_docx_text(dest))
            a["text"] = txt


def write(ctx, out_dir, jira=None):
    os.makedirs(out_dir, exist_ok=True)
    if jira is not None:
        fetch_attachments(jira, ctx, out_dir)
    base = os.path.join(out_dir, ctx["key"])
    with open(base + ".context.json", "w", encoding="utf-8") as fh:
        json.dump(ctx, fh, indent=2, ensure_ascii=False)
    with open(base + ".context.md", "w", encoding="utf-8") as fh:
        fh.write(to_markdown(ctx))
    return base + ".context.md"
