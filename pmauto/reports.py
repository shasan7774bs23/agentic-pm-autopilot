"""Builds the daily digest, the standup draft, the sprint-planning draft and release-notes drafts."""
import datetime as dt
from collections import defaultdict

from .activity import actions_between, forward_moves, is_person
from .model import ESTIMATE_STAGES, business_days
from .render import Doc, Link, Section


def _iso(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def _link(issue):
    return Link(issue.key, issue.url)


def _sp(v):
    return "" if v is None else (f"{v:g}")


def _pct(a, b):
    return f"{(100 * a / b):.0f}%" if b else "–"


def sprint_health_rows(snapshot, cfg):
    g = cfg["status_groups"]
    rows = []
    for s in snapshot.sprints:
        issues = [i for i in s["issues"] if i.type.lower() != "subtask"]
        start, end = _iso(s.get("startDate")), _iso(s.get("endDate"))
        total_bd = business_days(start, end, cfg) if start and end else 0
        used_bd = business_days(start, min(snapshot.now, end), cfg) if start and end else 0
        sp_total = sum(i.story_points or 0 for i in issues)
        true_done = [i for i in issues if i.status_l in g["true_done"]]
        jira_done = [i for i in issues if i.status_category == "done"]
        in_qa = [i for i in issues if i.status_l in g["qa"] + g["parked"]]
        carry = [i for i in issues if len(i.sprints) > 1]
        sp_true = sum(i.story_points or 0 for i in true_done)
        sp_jira = sum(i.story_points or 0 for i in jira_done)
        time_pct = 100 * used_bd / total_bd if total_bd else 0
        done_pct = 100 * sp_true / sp_total if sp_total else 0
        flag = "Behind" if done_pct + cfg.get("behind_margin_points", 20) < time_pct else "On track"
        rows.append([
            s["name"],
            f"{start:%d %b} – {end:%d %b}" if start and end else "",
            f"{used_bd:.1f} of {total_bd:.0f} ({time_pct:.0f}%)",
            len(issues), _sp(sp_total),
            f"{len(true_done)} issues · {_sp(sp_true)} SP ({_pct(sp_true, sp_total)})",
            f"{len(jira_done)} issues · {_sp(sp_jira)} SP ({_pct(sp_jira, sp_total)})",
            len(in_qa), len(carry), flag,
        ])
    return rows


def _all_issues(snapshot):
    seen = {}
    for s in snapshot.sprints:
        for i in s["issues"]:
            seen[i.key] = i
    for i in snapshot.open_issues:
        seen.setdefault(i.key, i)
    return list(seen.values())


def standup_rows(snapshot, cfg):
    """Yesterday = the person's own Jira actions (status moves, comments) on the previous workday,
    plus hours only where a worklog is authored by the person (time-tracking apps may log time under the app's account)."""
    g = cfg["status_groups"]
    tz = dt.timezone(dt.timedelta(hours=cfg["timezone_offset_hours"]))
    start = dt.datetime.combine(snapshot.prev_day, dt.time(0), tz)
    acts = actions_between(_all_issues(snapshot), start, start + dt.timedelta(days=1), cfg)
    hours = defaultdict(lambda: defaultdict(float))
    names = {aid: rec["name"] for aid, rec in acts.items()}
    for author_id, author, issue, h in snapshot.worklogs_prev_day:
        if is_person(author_id, author, cfg):
            hours[author_id][issue.key] += h
            names[author_id] = author
    today = defaultdict(list)
    seen = set()
    for s in snapshot.sprints:
        for i in s["issues"]:
            if i.key in seen or not is_person(i.assignee_id, i.assignee, cfg):
                continue
            seen.add(i.key)
            if i.status_l not in g["true_done"] + g["not_started"]:
                today[i.assignee_id].append(i)
                names[i.assignee_id] = i.assignee
    rows = []
    for aid in sorted(names, key=lambda a: names[a]):
        did = acts.get(aid, {}).get("items", {})
        y = "; ".join(f"{k}: {', '.join(v)}" for k, v in sorted(did.items())) or "no Jira activity"
        h = sum(hours[aid].values())
        t = ", ".join(f"{i.key} [{i.status}]" for i in today[aid]) or "–"
        rows.append([names[aid], y, f"{h:g}h" if h else "not logged by the person", t, ""])
    return rows


def build_completed_tasks(snapshot, cfg):
    """Work each person moved forward during the running sprint(s), from the Jira change history. Writes nothing."""
    starts = [_iso(s["startDate"]) for s in snapshot.sprints if s.get("startDate")]
    start = min(starts) if starts else snapshot.now - dt.timedelta(days=14)
    moves = forward_moves(_all_issues(snapshot), start, snapshot.now, cfg)
    names = ", ".join(s["name"] for s in snapshot.sprints)
    doc = Doc("Work moved forward this sprint",
              f"Sprints: {names}. Window: {start:%d %b} to {snapshot.now:%d %b %Y}. A ticket counts for a person when that "
              "person moved it forward (to review, QA, parked, ready to deploy or done) in the window.")
    rows = []
    for aid in sorted(moves, key=lambda a: moves[a]["name"]):
        rec = moves[aid]
        if not rec["moves"]:
            continue
        for key, (to, when, issue) in sorted(rec["moves"].items(), key=lambda kv: kv[1][1]):
            rows.append([rec["name"], _link(issue), issue.summary, f"{to} ({when:%d %b})", _sp(issue.story_points),
                         f"{issue.time_spent_h:g} h" if issue.time_spent_h else "", issue.status])
    closeouts = [f"{rec['name']}: {rec['closeouts']}" for rec in moves.values() if rec["closeouts"]]
    doc.sections.append(Section(
        "Moved forward", ("Moves from ready to deploy to done are release close-outs and are not counted: " + ", ".join(closeouts) + ". "
                          if closeouts else "") + f"{len(rows)} rows for {sum(1 for r in moves.values() if r['moves'])} people. "
        "Hours are time logged on the ticket by anyone.",
        ["Person", "Ticket", "Summary", "Moved to", "Story points", "Hours on ticket", "Status now"], rows))
    return doc


def build_daily(snapshot, findings, cfg):
    tz = dt.timezone(dt.timedelta(hours=cfg["timezone_offset_hours"]))
    local = snapshot.now.astimezone(tz)
    actions = [f for f in findings if f.severity == "action"]
    waiting_client = [f for f in findings if f.rule == "Waiting on client"]
    parked = [f for f in findings if f.rule == "Parked"]
    hygiene = [f for f in findings if f.severity == "info" and f.rule not in ("Waiting on client", "Parked")]
    counts = defaultdict(int)
    for f in actions:
        counts[f.rule] += 1
    headline = "; ".join(f"{n} × {r}" for r, n in counts.items()) or "no action items"

    doc = Doc(cfg["report_title"] + f" — {local:%a %d %b %Y}",
              f"Generated {local:%H:%M} ({cfg.get('timezone_label', 'local')} time) from live Jira data. {len(actions)} action item(s): {headline}.")

    doc.sections.append(Section(
        "1. Action needed", "Rules: client ticket untriaged ≥1 business day · client comment unanswered ≥1 business day · "
        "no status change ≥3 business days · Ready to Deploy without Fix Version.",
        ["Rule", "Ticket", "Summary", "Owner", "Age (bd)", "Detail"],
        [[f.rule, _link(f.issue), f.issue.summary, f.issue.assignee, f"{f.age_days:.1f}", f.detail] for f in actions]))

    doc.sections.append(Section(
        "2. Sprint health (true done)",
        "'True done' counts only the team's done statuses. Jira's own figure also counts statuses Jira treats as done "
        "(parked work), so it can overstate progress. 'Behind' means true-done SP trails elapsed time by more than "
        f"{cfg.get('behind_margin_points', 20)} points.",
        ["Sprint", "Dates", "Business days used", "Issues", "SP", "True done", "Jira 'done'", "In QA / parked",
         "Carried over", "Status"], sprint_health_rows(snapshot, cfg)))

    doc.sections.append(Section(
        f"3. New client tickets (last {cfg['new_client_ticket_window_days']} days)", "",
        ["Ticket", (cfg.get("client_reference") or {}).get("label", "Client ref"), "Summary", "Raised by", "Created", "Status", "Assignee", "Estimate"],
        [[_link(i), i.client_ref or "", i.summary, cfg["client_accounts"].get(i.reporter_id, i.reporter),
          f"{i.created.astimezone(tz):%d %b}", i.status, i.assignee,
          _estimate_text(i.estimate, cfg) if i.estimate else "none yet"]
         for i in snapshot.new_client_issues]))

    doc.sections.append(Section(
        "4. Waiting on the client", "", ["Ticket", "Summary", "Since (bd)", "Detail"],
        [[_link(f.issue), f.issue.summary, f"{f.age_days:.1f}", f.detail] for f in waiting_client]))

    buckets = defaultdict(int)
    for f in parked:
        buckets["10–20 bd" if f.age_days < 20 else "20–40 bd" if f.age_days < 40 else "40+ bd"] += 1
    doc.sections.append(Section(
        f"5. Parked ({len(parked)} tickets, 10+ business days)",
        "Parked statuses usually mean finished work waiting for client acceptance or release. Long stays here hide whether work "
        "is really finished. Age spread: " + (", ".join(f"{k}: {v}" for k, v in sorted(buckets.items())) or "none") + ".",
        ["Ticket", "Summary", "QA owner", "Age (bd)", "Since"],
        [[_link(f.issue), f.issue.summary, f.issue.assignee, f"{f.age_days:.0f}", f.detail.replace("Since ", "")]
         for f in sorted(parked, key=lambda f: -f.age_days)]))

    rel_rows = []
    today = local.date()
    for v in snapshot.versions:
        done = [i for i in v["issues"] if i.status_l in cfg["status_groups"]["true_done"]]
        rd = v.get("releaseDate")
        flag = ""
        if len(done) == len(v["issues"]):
            flag = "All issues Done: mark the version released in Jira"
        elif rd and dt.date.fromisoformat(rd) < today:
            flag = "Release date passed but version not released in Jira"
        elif not rd:
            flag = "No release date set"
        rel_rows.append([v["name"], rd or "–", f"{len(done)} of {len(v['issues'])}", flag])
    doc.sections.append(Section("6. Release status (unreleased Jira versions)", "",
                                ["Version", "Release date", "Done", "Check"], rel_rows))

    doc.sections.append(Section(
        "7. Board hygiene", "Fix these so the reports stay accurate. Old client items are older than 30 days.", ["Check", "Ticket", "Summary", "Owner", "Detail"],
        [[f.rule, _link(f.issue), f.issue.summary, f.issue.assignee, f.detail] for f in hygiene]))

    doc.sections.append(Section(
        f"8. Standup draft for today (time logged on {snapshot.prev_day:%a %d %b})",
        "Yesterday = the person's own status moves and comments in Jira. Hours show only where the person logged time "
        "under their own name; time logged by an app is not attributable to a person. Today = in-flight sprint tickets. "
        "Only the Blocker column needs typing.",
        ["Member", "Yesterday (Jira activity)", "Hours", "Today", "Blocker"], standup_rows(snapshot, cfg)))

    if snapshot.errors:
        doc.sections.append(Section("Run notes", bullets=snapshot.errors))
    return doc


def _estimate_text(est, cfg):
    """An estimate in the team's own labels, e.g. "DEV 1 / QA 0.5"."""
    labels = cfg.get("estimate_comment_labels") or {}
    name = {stage: " / ".join(l for l, st in labels.items() if st == stage) for stage in set(labels.values())}
    return " / ".join(f"{name.get(k, k)} {v:g}" for k, v in est.items())


def build_planning(snapshot, cfg):
    labels = cfg.get("estimate_comment_labels") or {}
    stages = [st for st in ESTIMATE_STAGES if st in labels.values()]
    heads = [" / ".join(l for l, st in labels.items() if st == stage) for stage in stages]
    doc = Doc("Sprint planning draft", "Generated from Jira. Estimates come from the team's estimate comments.")
    for s in snapshot.sprints:
        rows, tot = [], defaultdict(float)
        for i in sorted(s["issues"], key=lambda i: i.key):
            e = i.estimate or {}
            for k in stages:
                tot[k] += e.get(k, 0)
            tot["sp"] += i.story_points or 0
            rows.append([_link(i), i.summary, i.type, i.assignee, i.status, _sp(i.story_points),
                         *[_sp(e.get(k)) for k in stages]])
        rows.append(["Total", "", "", "", "", _sp(tot["sp"]), *[_sp(tot[k]) for k in stages]])
        doc.sections.append(Section(f"{s['name']} ({_iso(s['startDate']):%d %b} – {_iso(s['endDate']):%d %b})", "",
                                    ["Ticket", "Summary", "Type", "Assignee", "Status", "SP", *heads], rows))
    return doc


def build_release_notes(snapshot, cfg):
    docs = []
    for v in snapshot.versions:
        people = sorted({i.assignee for i in v["issues"] if i.assignee_id and i.assignee_id not in cfg["client_accounts"]})
        not_done = [i for i in v["issues"] if i.status_l not in cfg["status_groups"]["true_done"]]
        doc = Doc(f"{v['name']} — release notes draft",
                  f"Release date in Jira: {v.get('releaseDate') or 'not set'}. Add deployment scripts before publishing.")
        doc.sections.append(Section("Summary", bullets=[
            f"Description: {v.get('description') or '(write one line)'}",
            f"Contributors: {', '.join(people) or '–'}",
            f"Readiness: {len(v['issues']) - len(not_done)} of {len(v['issues'])} issues Done"]))
        doc.sections.append(Section("Issues", "", ["Ticket", "Summary", "Type", "Status"],
                                    [[_link(i), i.summary, i.type, i.status] for i in sorted(v["issues"], key=lambda i: i.key)]))
        docs.append((v["name"], doc))
    return docs
