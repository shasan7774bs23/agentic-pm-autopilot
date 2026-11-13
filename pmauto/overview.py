"""The startpm overview page: one screen per morning with every project, the new and client-updated
tickets, what GitHub did, and what is waiting on the laptop side. Read-only: builds HTML, changes nothing."""
import datetime as dt
import html
import json
import os
import re
import subprocess
from collections import Counter

from .model import business_days
from .render import SCRIPT

NAMES = {}   # project key -> short name; filled from config "project_names" by load_config
CRON_UTC = dt.time(3, 30)          # the digest workflow's schedule; set from config "digest_cron_utc" by load_config
TZ_LABEL = ["local"]               # set from config "timezone_label" by load_config
e = html.escape


# ---------- data ----------

def github_state(repo):
    """Recent workflow runs, the newest digest issues and the kill switch, via the gh CLI. Never raises."""
    def gh(*a):
        r = subprocess.run(["gh", *a, "-R", repo], capture_output=True, text=True, encoding="utf-8")
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip()[:200])
        return r.stdout

    out = {"repo": repo, "errors": [], "runs": [], "issues": [], "variables": {}}
    if not repo:
        out["disabled"] = True
        return out
    try:
        out["runs"] = json.loads(gh("run", "list", "--workflow", "daily-digest.yml", "--limit", "8", "--json",
                                    "databaseId,event,status,conclusion,createdAt,updatedAt,url"))
    except Exception as ex:
        out["errors"].append(f"runs: {ex}")
    try:
        out["issues"] = json.loads(gh("issue", "list", "--label", "digest", "--state", "all", "--limit", "5",
                                      "--json", "number,title,state,createdAt,url"))
    except Exception as ex:
        out["errors"].append(f"digest issues: {ex}")
    try:
        out["variables"] = {v["name"]: v["value"] for v in json.loads(gh("variable", "list", "--json", "name,value"))}
    except Exception as ex:
        out["errors"].append(f"variables: {ex}")
    return out


def _ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def run_delay_minutes(run):
    """How late a scheduled run started against its scheduled slot; None for manual runs."""
    if run["event"] != "schedule":
        return None
    started = _ts(run["createdAt"])
    slot = dt.datetime.combine(started.date(), CRON_UTC, dt.timezone.utc)
    return round((started - slot).total_seconds() / 60)


def client_updates(issues, cfg, since, exclude=()):
    """Open tickets a client account commented on or changed since `since`, newest first."""
    clients = cfg["client_accounts"]
    out = []
    for i in issues:
        if i.key in exclude:
            continue
        acts = [(c.created, clients.get(c.author_id, c.author), "comment", c.text.strip().replace("\n", " "))
                for c in i.comments if c.author_id in clients and c.created >= since]
        acts += [(h.created, clients.get(h.author_id, h.author), "change", f"{h.field}: {h.to_value}"[:200])
                 for h in i.history if h.author_id in clients and h.created >= since]
        if acts:
            acts.sort(key=lambda a: a[0])
            out.append({"issue": i, "last": acts[-1], "count": len(acts)})
    return sorted(out, key=lambda r: r["last"][0], reverse=True)


def sprint_stats(s, now, cfg):
    g = cfg["status_groups"]
    issues = [i for i in s["issues"] if i.type.lower() != "subtask"]
    start = _ts(s["startDate"]) if s.get("startDate") else None
    end = _ts(s["endDate"]) if s.get("endDate") else None
    total_bd = business_days(start, end, cfg) if start and end else 0
    used_bd = business_days(start, min(now, end), cfg) if start and end else 0
    sp = sum(i.story_points or 0 for i in issues)
    sp_true = sum(i.story_points or 0 for i in issues if i.status_l in g["true_done"])
    sp_jira = sum(i.story_points or 0 for i in issues if i.status_category == "done")
    time_pct = 100 * used_bd / total_bd if total_bd else 0
    true_pct = 100 * sp_true / sp if sp else 0
    return {"name": s["name"], "start": start, "end": end, "issues": len(issues), "sp": sp,
            "time_pct": time_pct, "true_pct": true_pct, "jira_pct": 100 * sp_jira / sp if sp else 0,
            "used_bd": used_bd, "total_bd": total_bd,
            "status": Counter(i.status for i in issues),
            "flag": "Behind" if true_pct + 20 < time_pct else "On track"}


def posted_by_me(issue, my_id, draft_path):
    """True when my account commented on the ticket after the draft file was last written.
    Catches posts made before the store tracked them (comments written before the tool recorded posts)."""
    if issue is None or not my_id or not os.path.exists(draft_path):
        return False
    written = dt.datetime.fromtimestamp(os.path.getmtime(draft_path), dt.timezone.utc)
    return any(c.author_id == my_id and c.created >= written for c in issue.comments)


def analysis_state(store, key, open_refs, analyses_dir, posted=False):
    t = store.ticket(key) or {}
    if posted or t.get("posted_comment_id"):
        return "ok", f"Comment posted ({t.get('verdict') or 'analysed'})"
    if key in open_refs:
        if os.path.exists(os.path.join(analyses_dir, f"{key}-analysis.md")):
            return "warn", "Analysed, waiting for review"
        return "warn", "On today's work list"
    if t.get("analysed_at"):
        if os.path.exists(os.path.join(analyses_dir, f"{key}-comment.md")):
            return "warn", f"{t.get('verdict') or 'Analysed'}, draft waiting for your 'post it'"
        return "ok", f"Analysed: {t.get('verdict') or '-'}"
    return "mute", "Not analysed yet"


def make_is_posted(store, snap, adir, my_id):
    by_key = {i.key: i for i in list(snap.open_issues) + list(snap.new_client_issues)}

    def is_posted(key, fname=None):
        """Posted = recorded by `pmauto post`, or my account commented after the draft was written."""
        fname = fname or f"{key}-comment.md"
        if fname == f"{key}-comment.md" and (store.ticket(key) or {}).get("posted_comment_id"):
            return True
        return posted_by_me(by_key.get(key), my_id, os.path.join(adir, fname))
    return is_posted


def drafts_waiting(store, analyses_dir, open_refs, is_posted):
    """Drafts I have reviewed and accepted that you have not posted yet. Drafts of tickets still on the
    work list are waiting for my review, not for your "post it", so they are left out here."""
    out = []
    for f in sorted(os.listdir(analyses_dir)):
        key = f.split("-meeting-comment.md")[0] if f.endswith("-meeting-comment.md") else             f[:-len("-comment.md")] if f.endswith("-comment.md") else None
        if key is None or is_posted(key, f):
            continue
        if f.endswith("-meeting-comment.md") or (key not in open_refs and (store.ticket(key) or {}).get("analysed_at")):
            out.append((key, f))
    return out


# ---------- html ----------

CSS = """
:root{--bg:#f5f6f8;--card:#fff;--ink:#1c2230;--mute:#5d6678;--line:#dce0e7;--acc:#2563eb;--ok:#15803d;--warn:#b45309;--bad:#b91c1c;
--b1:#2563eb;--b2:#16a34a;--b3:#9ca3af;--track:#e8ebf0;--s1:#94a3b8;--s2:#3b82f6;--s3:#8b5cf6;--s4:#f59e0b;--s5:#14b8a6;--s6:#16a34a;--s7:#64748b}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#10131a;--card:#191e28;--ink:#e6e9ef;--mute:#9aa3b5;--line:#2c3341;--acc:#7aa2ff;--ok:#4ade80;--warn:#fbbf24;--bad:#f87171;--b1:#60a5fa;--b2:#4ade80;--b3:#6b7280;--track:#262c38}}
:root[data-theme="dark"]{--bg:#10131a;--card:#191e28;--ink:#e6e9ef;--mute:#9aa3b5;--line:#2c3341;--acc:#7aa2ff;--ok:#4ade80;--warn:#fbbf24;--bad:#f87171;--b1:#60a5fa;--b2:#4ade80;--b3:#6b7280;--track:#262c38}
html,body{margin:0;padding:0;height:100%}body{overflow:hidden;background:var(--bg);color:var(--ink);font:14.5px/1.5 'Segoe UI',system-ui,sans-serif}
.scroll-root{height:100vh;height:100dvh;overflow-y:auto;-webkit-overflow-scrolling:touch;outline:none}
main{max-width:1240px;margin:0 auto;padding:18px 16px 60px}
h1{margin:0 0 2px;font-size:24px}h2{margin:28px 0 8px;font-size:19px;border-bottom:1px solid var(--line);padding-bottom:4px}
h3{margin:16px 0 6px;font-size:15.5px}a{color:var(--acc)}.mute{color:var(--mute)}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}.chip{border:1px solid var(--line);background:var(--card);border-radius:999px;padding:4px 12px;font-size:13px}
.ok{color:var(--ok);font-weight:600}.warn{color:var(--warn);font-weight:600}.bad{color:var(--bad);font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.card h3{margin:0 0 2px;font-size:18px}.big{font-size:26px;font-weight:700;line-height:1.1}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:10px 0}.kpi{border:1px solid var(--line);border-radius:8px;padding:6px 8px}
.kpi small{display:block;color:var(--mute);font-size:11.5px}
.bar{position:relative;height:12px;background:var(--track);border-radius:6px;overflow:hidden;margin:3px 0 8px}
.bar>span{position:absolute;left:0;top:0;bottom:0;border-radius:6px}
.lbl{display:flex;justify-content:space-between;gap:8px;font-size:12.5px;color:var(--mute)}
.stack{display:flex;height:16px;border-radius:6px;overflow:hidden;background:var(--track);margin:4px 0}
.stack>span{display:block;height:100%}.legend{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:12px;color:var(--mute)}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
.x{overflow-x:auto}table{border-collapse:collapse;width:100%;background:var(--card);font-size:13.5px}
th,td{border:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}th{background:var(--bg)}
.tag{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11.5px;font-weight:700;letter-spacing:.3px;color:#fff;background:var(--b1);vertical-align:2px}
.tag.upd{background:var(--s3)}.empty{color:var(--mute);font-style:italic;margin:4px 0 10px}
.sub{font-size:13px;font-weight:400}
"""

COLOURS = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)", "var(--s6)", "var(--s7)"]


def _bar(pct, colour, label, value):
    pct = max(0, min(100, pct))
    return (f'<div class="lbl"><span>{e(label)}</span><span>{e(value)}</span></div>'
            f'<div class="bar"><span style="width:{pct:.1f}%;background:{colour}"></span></div>')


def _stack(counter):
    total = sum(counter.values()) or 1
    items = counter.most_common()
    segs = "".join(f'<span title="{e(k)}: {v}" style="width:{100 * v / total:.2f}%;background:{COLOURS[n % 7]}"></span>'
                   for n, (k, v) in enumerate(items))
    leg = "".join(f'<span><i style="background:{COLOURS[n % 7]}"></i>{e(k)} {v}</span>' for n, (k, v) in enumerate(items))
    return f'<div class="stack">{segs}</div><div class="legend">{leg}</div>'


def _link(i):
    return f'<a href="{e(i.url)}" target="_blank">{e(i.key)}</a>'


def _local(t, tz):
    return t.astimezone(tz).strftime("%d %b %H:%M")


def _state(state):
    cls, text = state
    return f'<span class="{cls}">{e(text)}</span>'


def _table(head, rows):
    return ('<div class="x"><table><tr>' + "".join(f"<th>{h}</th>" for h in head) + "</tr>"
            + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows) + "</table></div>")


def build(snap, findings, cfg, store, gh, local, now=None, my_id=None, intel=None, exec_path=None):
    """local = {"mirrors": {...}, "versions": [...], "work": [...]} from the startpm run."""
    tz = dt.timezone(dt.timedelta(hours=cfg["timezone_offset_hours"]))
    now = now or snap.now
    today = now.astimezone(tz).date()
    adir = cfg["analyses_dir"]
    projects = cfg["projects"]
    open_refs = {w["ref"] for w in local["work"]}
    since = now - dt.timedelta(days=cfg["new_client_ticket_window_days"])
    new_issues = snap.new_client_issues
    new_keys = {i.key for i in new_issues}
    updates = client_updates(snap.open_issues, cfg, since, exclude=new_keys)
    actions = [f for f in findings if f.severity == "action"]
    digest_today = next((i for i in gh["issues"] if i["title"].endswith(today.strftime("%a %d %b %Y"))), None)
    by_key = {i.key: i for i in list(snap.open_issues) + list(snap.new_client_issues)}

    is_posted = make_is_posted(store, snap, adir, my_id)

    drafts = drafts_waiting(store, adir, open_refs, is_posted)
    drift = [v for v in local["versions"] if v.get("release_only_commits")]
    mism = [v for v in local["versions"] if v.get("match") is False]
    state = lambda key: _state(analysis_state(store, key, open_refs, adir, posted=is_posted(key)))

    h = [f"<h1>PM overview - {today:%a %d %b %Y}</h1>"
         f'<div class="mute">Built {_local(now, tz)} ({e(cfg["timezone_label"])} time) by <b>startpm</b> from live Jira, GitHub and this laptop. '
         f"Read-only: nothing was posted or changed.</div>"]
    chips = [
        (f'<span class="ok">Today\'s digest sent</span> ({_local(_ts(digest_today["createdAt"]), tz)})' if digest_today
         else '<span class="mute">No GitHub digest configured</span>' if gh.get("disabled")
         else '<span class="bad">Today\'s digest not on GitHub yet</span>'),
        f"<b>{len(new_issues)}</b> new client tickets (7 days)",
        f"<b>{len(updates)}</b> tickets updated by the client (7 days)",
        f"<b>{len(actions)}</b> action items",
        f'<b>{len(local["work"])}</b> on the work list',
        f'<b>{len(drafts)}</b> drafts waiting for "post it"',
        ('<span class="bad">Version MISMATCH</span>' if mism else '<span class="ok">Versions match</span>')
        + (f' · <span class="warn">release drift: {", ".join(NAMES.get(v["project"], v["project"]) for v in drift)}</span>' if drift else ""),
    ]
    if exec_path:
        chips.insert(0, f'<a href="file:///{e(exec_path.replace(os.sep, "/"))}"><b>Open the executive report</b></a>')
    if intel:
        hi = sum(1 for r in intel["risks"] if r["severity"] == "High")
        chips.insert(1, (f'<span class="bad">{hi} high risk(s)</span>' if hi else '<span class="ok">No high risks</span>')
                     + f' · {len(intel["risks"])} open risks, {len(intel.get("new_risks", []))} new')
    h.append('<div class="chips">' + "".join(f'<span class="chip">{c}</span>' for c in chips) + "</div>")

    # 1. projects at a glance
    h.append('<h2>1. Projects at a glance</h2><div class="grid">')
    for p in projects:
        sp = [sprint_stats(s, now, cfg) for s in snap.sprints if s["project"] == p]
        p_new = [i for i in new_issues if i.project == p]
        p_upd = [u for u in updates if u["issue"].project == p]
        p_act = [f for f in actions if f.issue.project == p]
        p_work = [w for w in local["work"] if w["ref"].startswith(p + "-")]
        card = [f'<div class="card"><h3>{NAMES.get(p, p)} <span class="mute sub">{p}</span></h3>']
        card.append('<div class="kpis">' + "".join(
            f'<div class="kpi"><small>{lab}</small><span class="big">{n}</span></div>'
            for lab, n in (("New tickets", len(p_new)), ("Client updates", len(p_upd)),
                           ("Action items", len(p_act)), ("Work list", len(p_work)))) + "</div>")
        for s in sp:
            fstat = {f["sprint"]: f["status"] for f in intel["forecast"]["sprints"]} if intel else {}
            if s["name"] in fstat:   # one verdict per sprint on the page: the forecast's (judged against the team's norm)
                flag = _pill(fstat[s["name"]], FC_CLS.get(fstat[s["name"]], "mute"))
            else:
                flag = f'<span class="{"bad" if s["flag"] == "Behind" else "ok"}">{s["flag"]}</span>'
            dates = f' · {s["start"]:%d %b} - {s["end"]:%d %b}' if s["start"] and s["end"] else ""
            card.append(f'<div><b>{e(s["name"])}</b>{dates} · {s["issues"]} issues · {s["sp"]:g} SP · {flag}</div>')
            card.append(_bar(s["time_pct"], "var(--b3)", "Sprint time used",
                             f'{s["used_bd"]:.1f} of {s["total_bd"]:.0f} days ({s["time_pct"]:.0f}%)'))
            card.append(_bar(s["true_pct"], "var(--b2)", "True done (done statuses)", f'{s["true_pct"]:.0f}% of SP'))
            card.append(_bar(s["jira_pct"], "var(--b1)", "Jira 'done' (also counts parked statuses)", f'{s["jira_pct"]:.0f}% of SP'))
            card.append('<div class="lbl"><span>Issues by status</span></div>' + _stack(s["status"]))
        if not sp:
            card.append('<div class="empty">No active sprint.</div>')
        if p_act:
            rules = Counter(f.rule for f in p_act)
            top = max(rules.values())
            card.append('<div class="lbl" style="margin-top:8px"><span><b>Action items by type</b></span></div>'
                        + "".join(_bar(100 * n / top, "var(--warn)", r, str(n)) for r, n in rules.most_common()))
        v = next((v for v in local["versions"] if v["project"] == p), None)
        if v:
            cls, word = ("bad", "MISMATCH") if v.get("match") is False else ("ok", "match") if v.get("match") else ("mute", "not checked")
            extra = (f' · <span class="warn">site runs {e(v["release_ref"])} (+{v["release_only_commits"]} commits)</span>'
                     if v.get("release_only_commits") else "")
            card.append(f'<div class="mute" style="margin-top:6px">Code {e(str(v.get("branch")))} = {e(str(v.get("code_version")))} · '
                        f'site = {e(str(v.get("site_version")))} · <span class="{cls}">{word}</span>{extra}</div>')
        card.append("</div>")
        h.append("".join(card))
    h.append("</div>")

    if intel:
        h += intel_sections(intel, cfg, exec_path)

    # 2. new tickets
    h.append('<h2>2. <span class="tag">NEW</span> New Tickets <span class="mute sub">raised by the client in the last 7 days</span></h2>')
    for p in projects:
        rows = [i for i in new_issues if i.project == p]
        h.append(f"<h3>{NAMES.get(p, p)} - New Tickets ({len(rows)})</h3>")
        if not rows:
            h.append('<div class="empty">No new client tickets.</div>')
            continue
        h.append(_table(["Ticket", "Summary", "Raised by", "Created", "Status", "Assignee", "Our analysis"], [
            [_link(i), e(i.summary), e(cfg["client_accounts"].get(i.reporter_id, i.reporter)), _local(i.created, tz),
             e(i.status), e(i.assignee), state(i.key)] for i in rows]))

    # 3. updated tickets
    h.append('<h2>3. <span class="tag upd">UPDATED</span> Updated Tickets <span class="mute sub">older open tickets the client '
             "commented on or changed in the last 7 days</span></h2>")
    for p in projects:
        rows = [u for u in updates if u["issue"].project == p]
        h.append(f"<h3>{NAMES.get(p, p)} - Updated Tickets ({len(rows)})</h3>")
        if not rows:
            h.append('<div class="empty">No client updates.</div>')
            continue
        body = []
        for u in rows:
            i, (when, who, kind, text) = u["issue"], u["last"]
            more = f' <span class="mute">(+{u["count"] - 1} more)</span>' if u["count"] > 1 else ""
            short = e(text[:160]) + ("…" if len(text) > 160 else "")
            body.append([_link(i), e(i.summary), f'{e(who)}<br><span class="mute">{_local(when, tz)}</span>',
                         f"{e(kind)}: {short}{more}", e(i.status), e(i.assignee), state(i.key)])
        h.append(_table(["Ticket", "Summary", "Last client update", "What", "Status", "Assignee", "Our analysis"], body))

    # 4. action items
    h.append("<h2>4. Action needed <span class=\"mute sub\">same rules as the digest, from live Jira now</span></h2>")
    for p in projects:
        rows = sorted([f for f in actions if f.issue.project == p], key=lambda f: -f.age_days)
        h.append(f"<h3>{NAMES.get(p, p)} ({len(rows)})</h3>")
        if not rows:
            h.append('<div class="empty">Nothing needs action.</div>')
            continue
        h.append(_table(["Rule", "Ticket", "Summary", "Owner", "Age (business days)", "Detail"], [
            [e(f.rule), _link(f.issue), e(f.issue.summary), e(f.issue.assignee), f"{f.age_days:.1f}", e(f.detail)] for f in rows]))

    # 5. github
    if gh.get("disabled"):
        h.append('<h2>5. What GitHub did</h2><div class="empty">No scheduled digest configured (optional: '
                 'set digest_repo to your private layer repo, see examples/private-layer).</div>')
        gh = dict(gh, runs=[], issues=[])
    else:
        h.append(f'<h2>5. What GitHub did <span class="mute sub">{e(gh["repo"])}</span></h2>')
    sw = gh["variables"].get("AGENT_ENABLED")
    lt = (dt.datetime.combine(dt.date.today(), CRON_UTC, dt.timezone.utc).astimezone(tz)).strftime("%H:%M")
    switch = ('<span class="bad">switched OFF (AGENT_ENABLED=false)</span>' if str(sw).lower() == "false"
              else '<span class="ok">on</span> (kill switch AGENT_ENABLED not set, so runs are on)' if sw is None
              else f'<span class="ok">on</span> (AGENT_ENABLED={e(str(sw))})')
    if not gh.get("disabled"):
        h.append(f'<div class="chips"><span class="chip">Automatic runs: {switch}</span>'
                 f'<span class="chip">Schedule: {lt} {e(TZ_LABEL[0])}, working days (GitHub may start it late)</span></div>')
    if gh["runs"]:
        body = []
        for r in gh["runs"]:
            d = run_delay_minutes(r)
            late = ("-" if d is None else '<span class="ok">on time</span>' if d <= 5
                    else f'<span class="{"bad" if d > 60 else "warn"}">{d // 60} h {d % 60} min</span>')
            res = r["conclusion"] or r["status"]
            cls = "ok" if res == "success" else "warn" if r["status"] != "completed" else "bad"
            trig = {"schedule": f"Schedule ({lt})", "workflow_dispatch": "By hand"}.get(r["event"], r["event"])
            body.append([f'<a href="{e(r["url"])}" target="_blank">{r["databaseId"]}</a>', e(trig),
                         _local(_ts(r["createdAt"]), tz), late, f'<span class="{cls}">{e(res)}</span>'])
        h.append("<h3>Daily digest runs</h3>" + _table(["Run", "Triggered by", f"Started ({e(TZ_LABEL[0])})", "Late by", "Result"], body))
    if gh["issues"]:
        h.append("<h3>Digests posted (each one emails you)</h3>" + _table(["Digest", f"Posted ({e(TZ_LABEL[0])})", "State"], [
            [f'<a href="{e(i["url"])}" target="_blank">{e(i["title"])}</a>', _local(_ts(i["createdAt"]), tz),
             e(i["state"].lower())] for i in gh["issues"]]))
    if not digest_today and not gh.get("disabled"):
        h.append(f'<p class="warn">Today\'s digest has not been posted yet. To send it now: '
                 f'<code>gh workflow run daily-digest.yml -R {e(gh["repo"])}</code> (emails you in about a minute).</p>')
    for err in gh["errors"]:
        h.append(f'<p class="bad">GitHub could not be read: {e(err)}</p>')

    # 6. laptop side
    h.append("<h2>6. On this laptop</h2>")
    h.append("<h3>Code mirrors</h3>" + _table(["Project", "Result"], [
        [NAMES.get(p, p), f'<span class="{"ok" if r.get("ok") else "bad"}">{"refreshed" if r.get("ok") else "FAILED"}</span> '
                          f'{e(str(r.get("head", "")))} {e(str(r.get("error", "")))}'] for p, r in local["mirrors"].items()]))
    for v in drift:
        h.append(f'<p class="warn">{NAMES.get(v["project"], v["project"])}: the live site runs {e(v["release_ref"])}, which has '
                 f'{v["release_only_commits"]} commit(s) not in {e(v["branch"])} '
                 f'(tickets: {e(", ".join(v["release_only_tickets"]) or "none named")}).</p>')
    h.append(f'<h3>Work list ({len(local["work"])})</h3>')
    h.append(_table(["Ticket", "Why", "Summary", "State"], [
        [e(w["ref"]), e(w["reason"] or ""), e(w["note"] or ""), state(w["ref"])] for w in local["work"]])
             if local["work"] else '<div class="empty">Nothing on the work list.</div>')
    h.append(f'<h3>Drafts waiting for your "post it" ({len(drafts)})</h3>')
    h.append(_table(["Ticket", "Draft file"], [[e(k), e(f)] for k, f in drafts]) if drafts else '<div class="empty">None.</div>')
    for x in snap.errors:
        h.append(f'<p class="warn">Jira note: {e(x)}</p>')

    body, n = "".join(h), [0]

    def renumber(m):
        n[0] += 1
        return f"<h2>{n[0]}. "
    body = re.sub(r"<h2>\d+\. ", renumber, body)
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>PM Overview</title><style>{CSS}</style></head><body><div class="scroll-root" tabindex="-1"><main>'
            + body + f"</main></div>{SCRIPT}</body></html>")


SEV_CLS = {"High": "bad", "Medium": "warn", "Low": "mute"}
FC_CLS = {"On track": "ok", "Usual spillover": "mute", "At risk": "warn", "Off track": "bad", "Overdue": "bad", "Late": "bad",
          "Ready (deploy pending)": "ok", "No date set": "mute"}


def _pill(text, cls):
    return f'<span class="{cls}">{e(text)}</span>'


def intel_sections(intel, cfg, exec_path=None):
    """Dashboard sections for forecasts, risks, effort and releases (built by pmauto.intel)."""
    fc, eff, risks = intel["forecast"], intel["effort"], intel["risks"]
    new_ids = set(intel.get("new_risks", []))
    h = ["<h2>9. Delivery forecast <span class=\"mute sub\">FORECAST from the team's own past sprints; "
         "CONFIRMED = dates set in Jira</span></h2><div class=\"grid\">"]
    for p in cfg["projects"]:
        v = fc["velocity"].get(p)
        card = [f'<div class="card"><h3>{NAMES.get(p, p)}</h3>']
        for s in [x for x in fc["sprints"] if x["project"] == p]:
            pct = 100 * s["sp_done"] / s["sp_total"] if s["sp_total"] else 0
            card.append(f'<div><b>{e(s["sprint"])}</b> · CONFIRMED end {e((s["end"] or "?")[:10])} · '
                        f'{_pill(s["status"], FC_CLS[s["status"]])}</div>')
            card.append(_bar(pct, "var(--b2)", "Done now (status Done)", f'{s["sp_done"]:g} of {s["sp_total"]:g} SP'))
            card.append(_bar(s["ready_pct"], "var(--s5)", "ready to deploy or done", f'{s["ready_pct"]}% of SP'))
            card.append(_bar(s["expected_pct"], "var(--b1)", "FORECAST at sprint end",
                             f'{s["expected_done"]:g} SP ({s["expected_pct"]}%) · ~{s["spill_sp"]:g} SP spill'
                             + (f' · team usually {s["norm_pct"]}%' if s.get("norm_pct") is not None else "")))
            if s.get("change"):
                card.append(f'<div class="mute">Changed: {e(s["change"])}</div>')
        if v:
            top = max(v["committed"] + [1])
            card.append('<div class="lbl" style="margin-top:8px"><span><b>Past sprints: completed vs committed SP</b></span></div>')
            for name, d, c in zip(v["names"], v["completed"], v["committed"]):
                card.append(f'<div class="lbl"><span>{e(name)}</span><span>{d:g} / {c:g}</span></div>'
                            f'<div class="bar"><span style="width:{100 * c / top:.1f}%;background:var(--b3)"></span>'
                            f'<span style="width:{100 * d / top:.1f}%;background:var(--b2)"></span></div>')
            ratio = f'{100 * v["completion_ratio"]:.0f}%' if v["completion_ratio"] is not None else "?"
            card.append(f'<div class="mute">Average {v["avg_completed"]:.1f} SP completed per sprint ({ratio} of commitment)'
                        + (f'; trend {v["trend"]}' if v["trend"] else "") + ".</div>")
        else:
            card.append('<div class="empty">No closed-sprint history yet.</div>')
        card.append("</div>")
        h.append("".join(card))
    h.append("</div>")

    rows = []
    for r in sorted(fc["releases"], key=lambda r: (r["confirmed"] is None, r["confirmed"] or dt.date.max)):
        rows.append([NAMES.get(r["project"], r["project"]), e(r["version"]),
                     f'CONFIRMED {r["confirmed"]:%d %b %Y}' if r["confirmed"] else '<span class="mute">not set</span>',
                     f'FORECAST {r["forecast"]:%d %b %Y}' if r["forecast"] else "-",
                     f'{r["issues"] - r["open_issues"]}/{r["issues"]}', _pill(r["status"], FC_CLS.get(r["status"], "mute")),
                     e(r["basis"]) + (f'<br><span class="mute">Changed: {e(r["change"])}</span>' if r.get("change") else "")])
    h.append('<h2>9. Releases <span class="mute sub">ready = ready to deploy or done; client UAT/approval not tracked in Jira</span></h2>')
    h.append(_table(["Project", "Version", "Planned", "Forecast", "Ready", "Status", "Basis"], rows) if rows
             else '<div class="empty">No unreleased versions with tickets.</div>')

    h.append(f'<h2>9. Risk register <span class="mute sub">{len(risks)} open · {len(new_ids)} new today · '
             f'{len(intel.get("closed_risks", []))} closed since last run</span></h2>')
    counts = Counter(r["severity"] for r in risks)
    h.append('<div class="chips">' + "".join(f'<span class="chip">{_pill(k, SEV_CLS[k])} {counts.get(k, 0)}</span>'
                                             for k in ("High", "Medium", "Low")) + "</div>")
    h.append(_table(["Sev", "Project", "Risk", "Likelihood", "Impact", "Evidence", "Recommended action", "Owner", "Since"], [
        [_pill(r["severity"], SEV_CLS[r["severity"]]) + (' <span class="tag">NEW</span>' if r["id"] in new_ids else ""),
         NAMES.get(r["project"], r["project"] or "All"), e(r["title"]), e(r["likelihood"] or ""), e(r["impact"]),
         e(r["evidence"]), e(r["action"]), e(r.get("owner") or ""), e(r.get("first_seen", ""))] for r in risks])
             if risks else '<div class="empty">No open risks.</div>')
    if intel.get("closed_risks"):
        h.append('<div class="mute">Closed since last run: ' + e("; ".join(c["title"] for c in intel["closed_risks"])) + "</div>")

    cov = eff.get("coverage", {})
    h.append(f'<h2>9. Effort and overruns <span class="mute sub">{cov.get("with_estimate", 0)} of {cov.get("tickets", 0)} current '
             f'tickets have a plan · {cov.get("hours_logged", 0):g} h logged on them</span></h2>')
    h.append('<div class="mute">Plan = the team&#39;s estimate comment (1 unit = '
             f'{cfg.get("estimate_unit_hours", 8)} h), else Jira&#39;s Original Estimate, else story points. Actual = time logged '
             'on the ticket (per ticket, not per person). An overrun is a question to ask, not a verdict.</div>')
    rows = []
    for a in eff["flagged"][:25]:
        i = a["issue"]
        pct = 100 * (a["ratio"] or 0)
        colour = "var(--bad)" if a["flag"] == "Significant overrun" else "var(--warn)" if a["flag"] == "Over estimate" else "var(--b1)"
        meter = (f'<div class="bar" style="min-width:120px"><span style="width:{min(pct, 100) / 2:.1f}%;background:var(--b2)"></span>'
                 f'<span style="left:{min(pct, 100) / 2:.1f}%;width:{max(0, min(pct, 200) - 100) / 2:.1f}%;background:{colour}"></span></div>'
                 if a["ratio"] is not None else "")
        rows.append([_link(i), e(a["flag"]), f'{a["spent"]:.1f} h / {a["planned"]:.1f} h' if a["planned"] else f'{a["spent"]:.1f} h / -',
                     meter + (f'{pct:.0f}%' if a["ratio"] is not None else ""), e(i.assignee), e(i.status)])
    h.append(_table(["Ticket", "Flag", "Logged / planned", "Used (bar = 200%)", "Assignee", "Status"], rows) if rows
             else '<div class="empty">No overruns or tickets close to their estimate.</div>')
    if eff["idle"]:
        h.append("<h3>Time logged, ticket not moving</h3>" + _table(["Ticket", "Detail", "Assignee"], [
            [_link(x["issue"]), e(x["detail"]), e(x["issue"].assignee)] for x in eff["idle"]]))
    for name, load in eff["load"].items():
        if load:
            h.append(f"<h3>Remaining planned work vs working days left · {e(name)} "
                     f'<span class="mute sub">(in QA only the QA part counts; {cfg.get("focus_hours_per_day", 6)} focus h/day)</span></h3>' + "".join(
                _bar(min(100, r["load_pct"] / 1.5), "var(--bad)" if r["flag"] else "var(--b1)",
                     f'{r["assignee"]} ({r["tickets"]} tickets' + (f', {r["unestimated"]} unestimated' if r["unestimated"] else "") + ")",
                     f'{r["remaining_h"]:g} h of ~{r["capacity_h"]:g} h ({r["load_pct"]}%)') for r in load))
    if eff["patterns"]:
        h.append("<h3>Repeat patterns (ask, don't judge)</h3><ul>" + "".join(
            f'<li>{e(p["assignee"])}: {p["over"]} of {p["of"]} estimated tickets ran over ({e(", ".join(p["keys"][:8]))})</li>'
            for p in eff["patterns"]) + "</ul>")
    for x in intel.get("errors", []):
        h.append(f'<p class="warn">Management layer note: {e(x)}</p>')
    return h


def write(page, cfg, now):
    tz = dt.timezone(dt.timedelta(hours=cfg["timezone_offset_hours"]))
    out = os.path.join(cfg["reports_dir"], now.astimezone(tz).strftime("%Y-%m-%d"))
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "PM-Overview.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    return path
