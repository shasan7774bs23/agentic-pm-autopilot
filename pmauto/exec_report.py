"""The executive daily report: a Project Manager's summary, not a Jira export.

Eight sections. Every item is tagged URGENT (act today), ATTENTION (needs a decision
or a nudge) or INFO. Lists are capped: the report points at the top items and says how many more exist.
Written as .md and self-contained .html; nothing is sent anywhere.
"""
import datetime as dt
import os

from .model import previous_workday
from .overview import NAMES
from .render import Doc, Link, Section, to_html, to_markdown

URGENT, ATTN, INFO = "URGENT", "ATTENTION", "INFO"
CAP = 8


def _n(p):
    return NAMES.get(p, p or "All")


def _more(items, cap=CAP):
    return items[:cap], (f"... and {len(items) - cap} more (see the PM Overview page)." if len(items) > cap else None)


def happened(snap, intel, cfg, since):
    """Status moves since `since`: completed work, significant moves, and new client tickets."""
    g = cfg["status_groups"]
    seen, moves = set(), []
    pool = [i for s in snap.sprints for i in s["issues"]] + list(snap.open_issues) + list(intel.get("recent_done", []))
    for i in pool:
        if i.key in seen:
            continue
        seen.add(i.key)
        for ev in i.history:
            if ev.field == "status" and ev.created and ev.created >= since:
                moves.append((i, ev))
    completed = [(i, ev) for i, ev in moves if ev.to_value.lower() in g["true_done"] + g["ready_to_deploy"]]
    to_qa = [(i, ev) for i, ev in moves if ev.to_value.lower() in g["qa"]]
    reopened = [(i, ev) for i, ev in moves if ev.to_value.lower() in g["not_started"] + g["in_progress"]
                and ev.from_value.lower() in g["qa"] + g["ready_to_deploy"] + g["true_done"]]
    return {"completed": completed, "to_qa": to_qa, "reopened": reopened, "moves": moves}


def build(snap, findings, intel, cfg, local, drafts=(), now=None):
    tz = dt.timezone(dt.timedelta(hours=cfg["timezone_offset_hours"]))
    now = now or snap.now
    today = now.astimezone(tz).date()
    prev = previous_workday(today, cfg["workdays"])
    since = dt.datetime.combine(prev, dt.time(0), tz)
    fc, eff, risks = intel["forecast"], intel["effort"], intel["risks"]
    hap = happened(snap, intel, cfg, since)
    actions = [f for f in findings if f.severity == "action"]
    high = [r for r in risks if r["severity"] == "High"]
    med = [r for r in risks if r["severity"] == "Medium"]
    new_ids = set(intel.get("new_risks", []))

    doc = Doc(f"Executive PM report — {today:%a %d %b %Y}",
              f"For {cfg['owner_name']}. Built {now.astimezone(tz):%d %b %H:%M} ({cfg['timezone_label']} time) from live Jira, the team's sprint history and "
              f"the code mirrors. Window: since {prev:%a %d %b}. Forecasts are marked FORECAST; dates set in Jira are CONFIRMED. "
              "Nothing was posted or changed.")

    # 1. Executive summary
    bullets = []
    for p in cfg["projects"]:
        sp = [s for s in fc["sprints"] if s["project"] == p]
        rel = [r for r in fc["releases"] if r["project"] == p and r["status"] not in ("No date set",)]
        pr = [r for r in risks if r["project"] == p]
        parts = []
        for s in sp:
            parts.append(f"{s['sprint']}: {s['sp_done']:g}/{s['sp_total']:g} SP Done ({s['ready_pct']}% ready to deploy or done), "
                         f"FORECAST {s['expected_pct']}% Done by {s['end'][:10] if s['end'] else '?'} ({s['status']})")
        for r in rel[:2]:
            parts.append(f"{r['version']}: {r['status']}")
        if pr:
            parts.append(f"{len(pr)} open risk(s), {sum(1 for r in pr if r['severity'] == 'High')} high")
        bullets.append(f"{_n(p)}: " + ("; ".join(parts) if parts else "no active sprint or dated release"))
    tag = URGENT if high else ATTN if med or actions else INFO
    headline = (f"[{tag}] {len(high)} high and {len(med)} medium risks; {len(actions)} action items from the daily rules; "
                f"{len(snap.new_client_issues)} new client tickets this week.")
    doc.sections.append(Section("1. Executive summary", headline, bullets=bullets))

    # 2. What happened
    rows = []
    for i, ev in sorted(hap["completed"], key=lambda x: x[1].created):
        rows.append([INFO, Link(i.key, i.url), i.summary[:70], f"→ {ev.to_value}", ev.author, f"{ev.created.astimezone(tz):%d %b %H:%M}"])
    for i, ev in hap["reopened"]:
        rows.append([ATTN, Link(i.key, i.url), i.summary[:70], f"{ev.from_value} → {ev.to_value} (moved back)", ev.author,
                     f"{ev.created.astimezone(tz):%d %b %H:%M}"])
    for i in snap.new_client_issues:
        if i.created >= since:
            rows.append([ATTN, Link(i.key, i.url), i.summary[:70], "NEW client ticket", i.reporter, f"{i.created.astimezone(tz):%d %b %H:%M}"])
    rows.sort(key=lambda r: {URGENT: 0, ATTN: 1, INFO: 2}[r[0]])   # moved back / new client tickets first
    changes = [s for s in fc["sprints"] + fc["releases"] if s.get("change")]
    note = (f"{len(hap['completed'])} ticket(s) reached ready to deploy or done, {len(hap['to_qa'])} went to QA, "
            f"{len(hap['reopened'])} moved back. Scope/forecast changes: {len(changes)}.")
    shown, more = _more(rows, 15)
    doc.sections.append(Section("2. What happened", note + (f" {more}" if more else ""),
                                ["Tag", "Ticket", "Summary", "Change", "By", "When"], shown,
                                empty="No completed work, moves back or new client tickets since the previous working day."))

    # 3. Items requiring my attention
    att = []
    for r in high:
        att.append([URGENT, _n(r["project"]), r["title"], r["action"]])
    if drafts:
        att.append([ATTN, "All", f"{len(drafts)} reviewed draft comment(s) waiting for your \"post it\"",
                    ", ".join(k for k, _ in drafts[:10])])
    for v in local.get("versions", []):
        if v.get("match") is False:
            att.append([URGENT, _n(v["project"]), "Live site version differs from the code", "Confirm the deployed build before more analysis."])
    unanswered = [f for f in actions if f.rule == "Client comment unanswered"]
    if unanswered:
        att.append([URGENT if len(unanswered) >= 3 else ATTN, "All", f"{len(unanswered)} client comment(s) unanswered",
                    ", ".join(f.issue.key for f in unanswered[:10])])
    untriaged = [f for f in actions if f.rule == "Untriaged client ticket"]
    if untriaged:
        att.append([ATTN, "All", f"{len(untriaged)} new client ticket(s) not triaged", ", ".join(f.issue.key for f in untriaged[:10])])
    rtd = [f for f in actions if f.rule == "Ready to Deploy without Fix Version"]
    if rtd:
        att.append([ATTN, "All", f"{len(rtd)} ticket(s) Ready to Deploy without a Fix Version", ", ".join(f.issue.key for f in rtd[:10])])
    doc.sections.append(Section("3. Items requiring my attention", "Only decisions, approvals, escalations or nudges that need you.",
                                ["Tag", "Project", "Item", "What to do / tickets"], att, empty="Nothing needs you today."))

    # 4. Delivery and sprint health
    rows = []
    for s in fc["sprints"]:
        t = URGENT if s["status"] == "Off track" else ATTN if s["status"] == "At risk" else INFO
        norm = f"; team usually {s['norm_pct']}%" if s.get("norm_pct") is not None else ""
        rows.append([t, _n(s["project"]), s["sprint"], f"CONFIRMED {s['end'][:10] if s['end'] else '?'}",
                     f"{s['sp_done']:g} / {s['sp_total']:g} (ready to deploy or done: {s['ready_pct']}%)", f"{s['bd_left']:g}",
                     f"FORECAST {s['expected_done']:g} SP ({s['expected_pct']}%{norm}) — {s['status']}", s.get("change") or "—"])
    vel_notes = []
    for p, v in fc["velocity"].items():
        if v:
            vel_notes.append(f"{_n(p)}: last {v['sprints']} sprints completed {', '.join(f'{x:g}' for x in v['completed'])} SP "
                             f"of {', '.join(f'{x:g}' for x in v['committed'])} committed"
                             + (f" ({100 * v['completion_ratio']:.0f}% on average)" if v['completion_ratio'] is not None else "")
                             + (f"; trend {v['trend']}" if v['trend'] else "") + ".")
        else:
            vel_notes.append(f"{_n(p)}: no closed-sprint history yet.")
    doc.sections.append(Section("4. Delivery and sprint health",
                                ("Done = only the team's done statuses, which is what Jira's sprint report counted "
                                 "as completed in past sprints (parked and ready-to-deploy work is NOT done here). "
                                 if cfg.get("velocity_done_key", "sp_true_done") == "sp_true_done" else
                                 "Done = Jira's done category (also counts parked statuses Jira treats as done). ")
                                + "Forecast = done so far + the team's historical pace × working days left.",
                                ["Tag", "Project", "Sprint", "Ends", "SP done / total", "Working days left", "Forecast", "Change since last run"],
                                rows, bullets=vel_notes, empty="No active sprints."))

    # 5. Risks and blockers
    rows = []
    for r in [x for x in risks if x["severity"] in ("High", "Medium")]:
        t = URGENT if r["severity"] == "High" else ATTN
        rows.append([t + (" · NEW" if r["id"] in new_ids else ""), _n(r["project"]), r["severity"], r["title"],
                     r["impact"], r["action"], r.get("owner") or "", r.get("first_seen", "")])
    closed = intel.get("closed_risks", [])
    low = [r for r in risks if r["severity"] == "Low"]
    shown, more = _more(rows, 12)
    doc.sections.append(Section("5. Risks and blockers",
                                f"{len(risks)} open risk(s): {len(high)} high, {len(med)} medium, {len(low)} low (low ones are on the PM "
                                f"Overview page). {len(new_ids)} new today; {len(closed)} closed since the last run"
                                + (": " + "; ".join(c["title"] for c in closed[:5]) if closed else "") + "." + (f" {more}" if more else ""),
                                ["Tag", "Project", "Severity", "Risk", "Impact", "Recommended action", "Owner", "Since"], shown,
                                empty="No high or medium risks."))

    # 6. Resource and time concerns
    rows = []
    for a in [x for x in eff["flagged"] if x["flag"] in ("Significant overrun", "Over estimate")][:CAP]:
        i = a["issue"]
        rows.append([ATTN if a["flag"] == "Significant overrun" else INFO, Link(i.key, i.url), a["flag"],
                     f"{a['spent']:.1f} h / {a['planned']:.1f} h ({a['ratio']:.1f}×, {a['source']})", i.assignee, i.status])
    for x in eff["idle"][:CAP]:
        i = x["issue"]
        rows.append([ATTN, Link(i.key, i.url), "Time logged, no progress", x["detail"], i.assignee, i.status])
    for name, load in eff["load"].items():
        for r in load:
            if r["flag"]:
                rows.append([ATTN, "Running sprints", "Over capacity", f"{r['remaining_h']:g} h left vs ~{r['capacity_h']:g} h available "
                             f"({r['load_pct']}%)", r["assignee"], f"{r['tickets']} tickets"])
    cov = eff.get("coverage", {})
    pat = [f"{p['assignee']}: {p['over']} of {p['of']} estimated tickets ran over ({', '.join(p['keys'][:6])})." for p in eff["patterns"]]
    doc.sections.append(Section("6. Resource and time concerns",
                                f"Planned effort comes from the team's estimate comments (1 unit = {cfg.get('estimate_unit_hours', cfg['hours_per_story_point'])} h), "
                                f"else Jira's Original Estimate, "
                                f"else story points × {cfg['hours_per_story_point']} h. Actual = time logged on the ticket. "
                                f"{cov.get('with_estimate', 0)} of {cov.get('tickets', 0)} current tickets have a plan to compare. "
                                "Hours are per ticket, not per person (time-tracking apps may log as the app), and an overrun is a question to ask, "
                                "not a verdict: scope change, rework and dependencies are the usual causes. "
                                "Capacity assumes " + f"{cfg.get('focus_hours_per_day', 6)} focus hours per person per working day.",
                                ["Tag", "Ticket / sprint", "Concern", "Numbers", "Assignee", "Status"], rows,
                                bullets=(["Repeat patterns (ask, don't judge): " + " ".join(pat)] if pat else None),
                                empty="No significant overruns, idle tickets or overloaded people."))

    # 7. Upcoming releases
    rows = []
    for r in sorted(fc["releases"], key=lambda r: (r["confirmed"] or dt.date.max)):
        t = URGENT if r["status"] in ("Overdue", "Late") else ATTN if r["status"] == "At risk" else INFO
        rows.append([t, _n(r["project"]), r["version"], f"CONFIRMED {r['confirmed']:%d %b}" if r["confirmed"] else "not set",
                     f"FORECAST {r['forecast']:%d %b}" if r["forecast"] else "—", f"{r['issues'] - r['open_issues']}/{r['issues']} ready",
                     r["status"], r["basis"]])
    doc.sections.append(Section("7. Upcoming releases",
                                "Ready = ready to deploy or done (status groups). UAT and client approval are not tracked in Jira fields here, so "
                                "'Ready' means the team's work is finished, not that the client has signed off. Each forecast assumes the "
                                "project's whole flow goes to that release; releases share the same people, so read them together.",
                                ["Tag", "Project", "Version", "Planned", "Forecast", "Tickets", "Status", "Basis"], rows,
                                empty="No unreleased versions with tickets."))

    # 8. Recommended actions
    recs = []
    for r in high:
        recs.append(f"[{URGENT}] {r['title']} — {r['action']}")
    if unanswered:
        recs.append(f"[{URGENT if len(unanswered) >= 3 else ATTN}] Reply to the client on {', '.join(f.issue.key for f in unanswered[:6])}.")
    if drafts:
        recs.append(f"[{ATTN}] Review and say \"post it\" for the {len(drafts)} waiting draft comment(s).")
    for r in med[:6]:
        recs.append(f"[{ATTN}] {r['title']} — {r['action']}")
    if not recs:
        recs.append(f"[{INFO}] Nothing urgent. Check the PM Overview for low risks and trends.")
    doc.sections.append(Section("8. Recommended actions", "In priority order.", bullets=recs))

    if intel.get("errors"):
        doc.sections.append(Section("Run notes", "Parts that could not be built this run:", bullets=intel["errors"]))
    return doc


def write(doc, cfg, now):
    tz = dt.timezone(dt.timedelta(hours=cfg["timezone_offset_hours"]))
    day = now.astimezone(tz).strftime("%Y-%m-%d")
    out = os.path.join(cfg["reports_dir"], day)
    os.makedirs(out, exist_ok=True)
    base = os.path.join(out, f"{day}_Executive-Report")
    with open(base + ".md", "w", encoding="utf-8") as fh:
        fh.write(to_markdown(doc))
    html_doc = to_html(doc).replace("<td>URGENT", '<td style="color:#b91c1c;font-weight:700">URGENT') \
                           .replace("<td>ATTENTION", '<td style="color:#b45309;font-weight:700">ATTENTION')
    with open(base + ".html", "w", encoding="utf-8") as fh:
        fh.write(html_doc)
    return base + ".html"
