"""End-of-day ticket report: every client ticket on the work list with its Jira status,
the analysis verdict, what was posted, and the next action. Written as .md and .html.
"""
import datetime as dt
import html
import os
import re

from .model import parse_ts
from .settings import statuses

NEXT = {
    "READY": "Review the spec; post it; assign a developer",
    "NEEDS CLARIFICATION": "Review the questions; post them; park the ticket until the client answers",
    "BLOCKED": "Resolve the blocker named in the analysis",
}


IN_FLIGHT = ("in_progress", "review", "qa", "parked", "ready_to_deploy", "true_done")


def _next(t, verdict, status, cfg):
    if t.get("posted_comment_id"):
        return "Done for now - posted"
    if status.lower() in statuses(cfg, *IN_FLIGHT):
        # Work has already started: parking it again would undo the team's progress.
        if verdict == "NEEDS CLARIFICATION":
            return f"Already {status}: post the question to confirm with the client, but do not move the ticket; tell the developer/QA"
        if verdict in NEXT:
            return f"Already {status}: use the analysis as a check of what was built"
    if verdict == "NEEDS CLARIFICATION" and cfg.get("clarification_sprint"):
        return f"Review the questions; post them; move the ticket to the {cfg['clarification_sprint']} sprint"
    return NEXT.get(verdict, "Analyse")


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (OSError, TypeError):
        return ""


def _assessment(text):
    """First sentence of the Assessment section and the machine-readable fields."""
    out = {}
    m = re.search(r"^VERDICT:\s*(.+)$", text, re.M)
    if m:
        out["verdict"] = m.group(1).strip()
    m = re.search(r"^QUESTIONS:\s*(\d+)", text, re.M)
    if m:
        out["questions"] = int(m.group(1))
    m = re.search(r"^## Assessment[^\n]*\n+(.+?)(?:\n\n|\n## )", text, re.S | re.M)
    if m:
        out["reason"] = re.sub(r"\s+", " ", m.group(1)).strip()[:400]
    return out


def collect(jira, store, cfg, keys):
    raws = jira.search(f"key in ({', '.join(keys)})",
                       ["summary", "status", "assignee", "priority", "updated", "issuetype", cfg["fields"]["sprint"]]) if keys else []
    by_key = {r["key"]: r for r in raws}
    rows = []
    for key in keys:
        r = by_key.get(key, {"fields": {}})
        f = r["fields"]
        t = store.ticket(key) or {}
        analysis_file = t.get("analysis_file") or os.path.join(cfg["analyses_dir"], f"{key}-analysis.md")
        comment_file = os.path.join(cfg["analyses_dir"], f"{key}-comment.md")
        a = _assessment(_read(analysis_file))
        verdict = t.get("verdict") or a.get("verdict")
        sprints = f.get(cfg["fields"]["sprint"]) or []
        rows.append({
            "key": key, "url": f"{jira.base}/browse/{key}", "summary": f.get("summary", "(not found in Jira)"),
            "type": (f.get("issuetype") or {}).get("name", ""), "status": (f.get("status") or {}).get("name", "?"),
            "assignee": (f.get("assignee") or {}).get("displayName", "Unassigned"),
            "priority": (f.get("priority") or {}).get("name", ""),
            "sprint": sprints[-1]["name"] if sprints else "Backlog",
            "updated": parse_ts(f["updated"]).strftime("%d %b %H:%M") if f.get("updated") else "",
            "verdict": verdict or "Not analysed yet", "questions": a.get("questions"),
            "reason": a.get("reason", ""), "analysed": (t.get("analysed_at") or "")[:16].replace("T", " "),
            "posted": t.get("posted_comment_id"), "analysis_file": analysis_file if os.path.exists(analysis_file) else None,
            "comment_file": comment_file if os.path.exists(comment_file) else None,
            "next": _next(t, verdict, (f.get("status") or {}).get("name", ""), cfg),
        })
    return rows


def to_markdown(day, rows, audit, failures):
    counts = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    lines = [f"# {TEAM} end-of-day ticket report - {day}", "",
             "**Summary:** " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) + f" ({len(rows)} tickets)", ""]
    if failures:
        lines += ["## Failures today", ""] + [f"- {a['ts'][11:16]} {a['action']} {a['ref'] or ''}: {a['detail'][:160]}"
                                               for a in failures] + [""]
    lines += ["## All tickets", "",
              "| Ticket | Summary | Jira status | Sprint | Assignee | Verdict | Qs | Posted | Next action |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| [{r['key']}]({r['url']}) | {r['summary'][:70]} | {r['status']} | {r['sprint']} | {r['assignee']} | "
                     f"{r['verdict']} | {'' if r['questions'] is None else r['questions']} | {'yes' if r['posted'] else 'no'} | {r['next']} |")
    lines += ["", "## Ticket details", ""]
    for r in rows:
        lines += [f"### {r['key']} - {r['summary']}", "",
                  f"- **Jira:** {r['status']} - {r['sprint']} - {r['assignee']} - priority {r['priority'] or 'n/a'} - updated {r['updated']}",
                  f"- **Assessment:** {r['verdict']}" + (f" - {r['reason']}" if r['reason'] else ""),
                  f"- **Analysed:** {r['analysed'] or 'not yet'}; **questions:** {'' if r['questions'] is None else r['questions']}; "
                  f"**comment posted:** {r['posted'] or 'no'}",
                  f"- **Files:** analysis {r['analysis_file'] or '-'}; Jira comment draft {r['comment_file'] or '-'}",
                  f"- **Next action:** {r['next']}", ""]
    lines += ["## Actions logged today", ""] + [f"- {a['ts'][11:16]} UTC - {a['actor']} - {a['action']} {a['ref'] or ''} - {a['outcome']}"
                                                for a in audit]
    return "\n".join(lines) + "\n"


def to_html(day, rows, audit, failures):
    e = html.escape
    color = {"READY": "#15803d", "NEEDS CLARIFICATION": "#b45309", "BLOCKED": "#b91c1c"}
    trs = "".join(
        f"<tr><td><a href='{e(r['url'])}'>{e(r['key'])}</a></td><td>{e(r['summary'])}</td><td>{e(r['status'])}</td>"
        f"<td>{e(r['sprint'])}</td><td>{e(r['assignee'])}</td><td style='color:{color.get(r['verdict'], '#5b6475')};font-weight:700'>"
        f"{e(r['verdict'])}</td><td>{'' if r['questions'] is None else r['questions']}</td><td>{'yes' if r['posted'] else 'no'}</td>"
        f"<td>{e(r['next'])}</td></tr>" for r in rows)
    details = "".join(
        f"<section><h3><a href='{e(r['url'])}'>{e(r['key'])}</a> - {e(r['summary'])}</h3><p><b>Jira:</b> {e(r['status'])} - "
        f"{e(r['sprint'])} - {e(r['assignee'])} - priority {e(r['priority'] or 'n/a')} - updated {e(r['updated'])}</p>"
        f"<p><b>Assessment:</b> {e(r['verdict'])}{(' - ' + e(r['reason'])) if r['reason'] else ''}</p>"
        f"<p><b>Next action:</b> {e(r['next'])}</p><p class='m'>analysis: {e(r['analysis_file'] or '-')}<br>comment draft: "
        f"{e(r['comment_file'] or '-')}</p></section>" for r in rows)
    fail = "".join(f"<li>{e(a['ts'][11:16])} {e(a['action'])} {e(a['ref'] or '')}: {e(a['detail'][:160])}</li>" for a in failures)
    log = "".join(f"<li>{e(a['ts'][11:16])} UTC - {e(a['actor'])} - {e(a['action'])} {e(a['ref'] or '')} - {e(a['outcome'])}</li>"
                  for a in audit)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(TEAM)} ticket report {e(day)}</title><style>
:root{{--bg:#f6f7f9;--card:#fff;--ink:#1d2330;--mute:#5b6475;--line:#d9dde5}}
@media (prefers-color-scheme:dark){{:root{{--bg:#11141a;--card:#1a1f29;--ink:#e7eaf0;--mute:#a0a8b8;--line:#2d3442}}}}
html,body{{margin:0;height:100%}}body{{overflow:hidden;background:var(--bg);color:var(--ink);font:14px/1.5 'Segoe UI',system-ui,sans-serif}}
.s{{height:100vh;height:100dvh;overflow-y:auto;outline:none}}main{{max-width:1200px;margin:0 auto;padding:20px 16px 60px}}
.t{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;background:var(--card)}}th,td{{border:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}}
section{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px;margin:10px 0}}h3{{margin:0 0 6px}}
.m{{color:var(--mute);font-size:12px}}a{{color:#2563eb}}</style></head><body><div class="s" tabindex="-1"><main>
<h1>{e(TEAM)} end-of-day ticket report - {e(day)}</h1>
{('<h2>Failures today</h2><ul>' + fail + '</ul>') if fail else ''}
<h2>All tickets ({len(rows)})</h2><div class="t"><table><tr><th>Ticket</th><th>Summary</th><th>Jira status</th><th>Sprint</th><th>Assignee</th>
<th>Verdict</th><th>Qs</th><th>Posted</th><th>Next action</th></tr>{trs}</table></div>
<h2>Ticket details</h2>{details}<h2>Actions logged today</h2><ul>{log}</ul></main></div>
<script>var s=document.querySelector('.s');s.focus();addEventListener('keydown',function(e){{var h=s.clientHeight,m={{PageDown:h*.9,PageUp:-h*.9,ArrowDown:40,ArrowUp:-40,' ':h*.9}};
if(e.key==='End'){{s.scrollTo(0,s.scrollHeight)}}else if(e.key==='Home'){{s.scrollTo(0,0)}}else if(m[e.key]&&document.activeElement!==s){{s.scrollBy(0,m[e.key])}}}});</script>
</body></html>"""


TEAM = "Team"   # set from config by build()


def build(jira, store, cfg, all_open=True):
    global TEAM
    TEAM = cfg.get("team_name", "Team")
    tz = dt.timezone(dt.timedelta(hours=cfg["timezone_offset_hours"]))
    now = dt.datetime.now(tz)
    day = now.strftime("%Y-%m-%d")
    start_utc = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(dt.timezone.utc).isoformat()
    keys = []
    for w in store.db.execute("SELECT ref FROM work_items WHERE kind='ticket' AND (status='open' OR closed_at >= ?) "
                              "ORDER BY id", (start_utc,)):
        if w["ref"] not in keys:
            keys.append(w["ref"])
    rows = collect(jira, store, cfg, keys)
    audit = store.audit_rows(since=start_utc)
    failures = [a for a in audit if a["outcome"] == "failed"]
    out = os.path.join(cfg["reports_dir"], day)
    os.makedirs(out, exist_ok=True)
    md, page = to_markdown(day, rows, audit, failures), to_html(day, rows, audit, failures)
    paths = []
    for ext, text in (("md", md), ("html", page)):
        path = os.path.join(out, f"{day}_ticket-report.{ext}")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        paths.append(path)
    store.audit("claude", "end_of_day_report", None, "ok", tickets=len(rows), files=paths)
    return paths, rows
