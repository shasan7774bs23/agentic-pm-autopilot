"""History: the data forecasting and trend checks stand on.

Two sources, both read-only:
- a daily snapshot of every active sprint, project and unreleased version (taken by startpm),
- a one-off backfill of closed sprints from Jira's own sprint report (velocity history).

Rows go into the store's `snapshots` table, keyed (day, scope):
  sprint:<id>        one row per day while the sprint runs
  project:<KEY>      one row per day
  version:<KEY>:<id> one row per day while unreleased
  velocity:<KEY>     one row per CLOSED sprint, day = the sprint's completion date
"""
import datetime as dt
import re

from .model import business_days


def _ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def _local_day(now, cfg):
    return now.astimezone(dt.timezone(dt.timedelta(hours=cfg["timezone_offset_hours"]))).date().isoformat()


def _val(x):
    return (x or {}).get("value") or 0.0


def sprint_metrics(s, now, cfg):
    g = cfg["status_groups"]
    issues = [i for i in s["issues"] if i.type.lower() != "subtask"]
    start, end = _ts(s.get("startDate")), _ts(s.get("endDate"))
    sp = lambda pred: round(sum(i.story_points or 0 for i in issues if pred(i)), 3)
    finished = g["true_done"] + g["ready_to_deploy"]

    def reached(i):
        return bool(start) and i.status_l in finished and any(
            ev.field == "status" and ev.created >= start and ev.to_value.lower() in finished for ev in i.history)
    return {
        "project": s["project"], "name": s["name"], "start": s.get("startDate"), "end": s.get("endDate"),
        "issues": len(issues),
        "sp_total": sp(lambda i: True),
        "sp_true_done": sp(lambda i: i.status_l in g["true_done"]),
        "sp_jira_done": sp(lambda i: i.status_category == "done"),
        "sp_ready_or_done": sp(lambda i: i.status_l in g["true_done"] + g["ready_to_deploy"]),
        "sp_reached_ready": sp(reached),
        "issues_reached_ready": sum(1 for i in issues if reached(i)),
        "no_points": sum(1 for i in issues if i.story_points is None),
        "hours_spent": round(sum(i.time_spent_h for i in issues), 2),
        "bd_used": round(business_days(start, min(now, end), cfg), 2) if start and end else None,
        "bd_total": round(business_days(start, end, cfg), 2) if start and end else None,
        "keys": sorted(i.key for i in issues),
        "status": {st: sum(1 for i in issues if i.status == st) for st in sorted({i.status for i in issues})},
    }


def version_metrics(v, cfg):
    g = cfg["status_groups"]
    issues = v["issues"]
    return {
        "project": v["project"], "name": v["name"], "release_date": v.get("releaseDate"),
        "issues": len(issues),
        "done": sum(1 for i in issues if i.status_l in g["true_done"] + g["ready_to_deploy"]),
        "in_qa": sum(1 for i in issues if i.status_l in g["qa"]),
        "not_started": sum(1 for i in issues if i.status_l in g["not_started"]),
        "sp_total": round(sum(i.story_points or 0 for i in issues), 3),
        "sp_open": round(sum(i.story_points or 0 for i in issues
                             if i.status_l not in g["true_done"] + g["ready_to_deploy"]), 3),
        "keys": sorted(i.key for i in issues),
    }


def take_snapshot(store, snap, cfg, findings=()):
    """Writes today's rows. Safe to call more than once a day: the last call wins."""
    day = _local_day(snap.now, cfg)
    n = 0
    for s in snap.sprints:
        store.snapshot(day, f"sprint:{s['id']}", sprint_metrics(s, snap.now, cfg))
        n += 1
    for v in snap.versions:
        store.snapshot(day, f"version:{v['project']}:{v['id']}", version_metrics(v, cfg))
        n += 1
    for p in cfg["projects"]:
        mine = [i for i in snap.open_issues if i.project == p]
        f = [x for x in findings if x.issue.project == p]
        store.snapshot(day, f"project:{p}", {
            "open_watched": len(mine),
            "new_client": sum(1 for i in snap.new_client_issues if i.project == p),
            "actions": sum(1 for x in f if x.severity == "action"),
            "rules": {r: sum(1 for x in f if x.rule == r) for r in sorted({x.rule for x in f})},
        })
        n += 1
    return n


def sprint_report(jira, board, sprint_id):
    c = jira.get("/rest/greenhopper/1.0/rapid/charts/sprintreport", rapidViewId=board, sprintId=sprint_id)["contents"]
    done, not_done, punted = (_val(c.get("completedIssuesEstimateSum")), _val(c.get("issuesNotCompletedEstimateSum")),
                              _val(c.get("puntedIssuesEstimateSum")))
    added = list((c.get("issueKeysAddedDuringSprint") or {}).keys())
    return {"sp_completed": done, "sp_not_completed": not_done, "sp_removed": punted,
            "sp_committed": round(done + not_done + punted, 3),
            "issues_completed": len(c.get("completedIssues") or []),
            "issues_not_completed": len(c.get("issuesNotCompletedInCurrentSprint") or []),
            "issues_added_mid_sprint": len(added)}


def is_delivery_sprint(s, cfg):
    """Delivery sprints are dated; the clarification sprint (a parking place) is not one, and when the team set
    "delivery_sprint_pattern", the name must also match it."""
    if not (s.get("startDate") and s.get("completeDate")):
        return False
    if cfg.get("clarification_sprint") and s["name"].strip().lower() == cfg["clarification_sprint"].strip().lower():
        return False
    pattern = cfg.get("delivery_sprint_pattern")
    return bool(re.search(pattern, s["name"])) if pattern else True


def backfill(jira, store, cfg, last_n=12, log=print):
    """Velocity history from Jira's sprint report for the last N closed delivery sprints per board.
    Already-stored sprints are skipped, so this is cheap to run every morning."""
    have = {(m.get("sprint_id")) for p in cfg["projects"] for _, m in store.history(f"velocity:{p}")}
    added = 0
    for project, board in cfg["boards"].items():
        closed = [s for s in jira.paged_values(f"/rest/agile/1.0/board/{board}/sprint", state="closed")
                  if is_delivery_sprint(s, cfg) and s.get("originBoardId", board) == board]
        closed.sort(key=lambda s: s["completeDate"])
        for s in closed[-last_n:]:
            if s["id"] in have:
                continue
            try:
                m = sprint_report(jira, board, s["id"])
            except Exception as ex:   # one bad sprint must not stop the rest
                log(f"history: sprint report for {s['name']} failed: {ex}")
                continue
            start, end = _ts(s["startDate"]), _ts(s["completeDate"])
            m.update({"sprint_id": s["id"], "name": s["name"], "start": s["startDate"], "complete": s["completeDate"],
                      "bd_length": round(business_days(start, end, cfg), 2)})
            store.snapshot(_local_day(end, cfg), f"velocity:{project}", m)
            added += 1
    return added
