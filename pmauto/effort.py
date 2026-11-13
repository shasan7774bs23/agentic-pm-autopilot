"""Effort and overrun tracking: planned effort beside time actually logged, per ticket.

Planned effort, first match wins:
  1. the team's estimate comment (labels in config "estimate_comment_labels"), in units of `estimate_unit_hours`
  2. Jira's Original Estimate field
  3. story points × `hours_per_story_point`
Actual effort = Jira "Time Spent" on the ticket (time-tracking apps write it there too).

Limits, stated in every output: time is per TICKET, not per person (time-tracking apps may log it under
the app's name), so a person view uses the ticket's assignee, which is not the same as who logged the hours.
An overrun is a signal to look at, never a verdict on a person: scope changes, rework and
dependencies are the usual causes, and the report says so.
"""
from collections import defaultdict

from .model import business_days
from .settings import statuses


def planned_hours(issue, cfg):
    unit = cfg.get("estimate_unit_hours", cfg["hours_per_story_point"])
    if issue.estimate:
        return sum(issue.estimate.values()) * unit, "estimate comment"
    if issue.orig_estimate_h:
        return issue.orig_estimate_h, "Original Estimate"
    if issue.story_points:
        return issue.story_points * cfg["hours_per_story_point"], "story points"
    return None, None


def assess(issue, now, cfg):
    """One ticket's effort picture, or None when there is nothing to compare."""
    planned, source = planned_hours(issue, cfg)
    spent = issue.time_spent_h or 0
    if not planned:
        return None if spent < 8 else {
            "issue": issue, "planned": None, "source": None, "spent": spent, "ratio": None,
            "flag": "No estimate", "detail": f"{spent:.1f} h logged with no estimate to compare against"}
    ratio = spent / planned
    g = cfg["status_groups"]
    finished = issue.status_l in g["true_done"] + g["ready_to_deploy"]
    flag = None
    if ratio >= 1.5 and spent - planned >= 4:
        flag = "Significant overrun"
    elif ratio > 1.0 and spent - planned >= 2:
        flag = "Over estimate"
    elif not finished and ratio >= 0.8 and issue.status_l in statuses(cfg, "not_started", "in_progress"):
        flag = "Likely to exceed"
    detail = f"{spent:.1f} h logged against {planned:.1f} h planned ({source}); {issue.status}"
    return {"issue": issue, "planned": planned, "source": source, "spent": spent, "ratio": ratio,
            "flag": flag, "detail": detail}


def time_without_progress(issues, now, cfg, jira=None, min_hours=8, min_bd=5):
    """Hours keep being logged while the status has not moved for `min_bd` working days."""
    g = cfg["status_groups"]
    out = []
    for i in issues:
        if i.status_l in g["true_done"] + g["ready_to_deploy"] + g["parked"] + g["not_started"]:
            continue
        idle = business_days(i.status_since, now, cfg)
        if idle < min_bd or not i.time_spent_h:
            continue
        since_hours = None
        if jira is not None:
            try:
                logs = jira.worklogs(i.key, int(i.status_since.timestamp() * 1000))
                since_hours = sum(w["timeSpentSeconds"] for w in logs) / 3600
            except Exception:
                since_hours = None
        if since_hours is not None and since_hours >= min_hours:
            out.append({"issue": i, "hours_since": since_hours, "idle_bd": idle,
                        "detail": f"{since_hours:.1f} h logged since it entered '{i.status}' {idle:.0f} working days ago"})
    return out


def assignee_patterns(assessed, min_tickets=3, min_share=0.5):
    """Assignees whose estimated tickets repeatedly ran over. A prompt to talk, not a judgement."""
    by = defaultdict(lambda: {"n": 0, "over": 0, "keys": []})
    for a in assessed:
        if not a or a["planned"] is None:
            continue
        rec = by[a["issue"].assignee]
        rec["n"] += 1
        if a["flag"] in ("Over estimate", "Significant overrun"):
            rec["over"] += 1
            rec["keys"].append(a["issue"].key)
    out = []
    for who, r in by.items():
        if who != "Unassigned" and r["over"] >= min_tickets and r["over"] / r["n"] >= min_share:
            out.append({"assignee": who, "over": r["over"], "of": r["n"], "keys": r["keys"]})
    return sorted(out, key=lambda x: -x["over"])


def remaining_hours(i, cfg):
    """Planned hours still ahead of a ticket, by stage: in QA only the QA part is left, in review review + QA."""
    planned, _ = planned_hours(i, cfg)
    if planned is None:
        return None
    unit = cfg.get("estimate_unit_hours", cfg["hours_per_story_point"])
    e = i.estimate or {}
    if i.status_l in cfg["status_groups"]["qa"]:
        return e.get("qa", 0) * unit if e else planned * cfg.get("remaining_share_in_qa", 0.25)
    if i.status_l in cfg["status_groups"]["review"]:
        return (e.get("review", 0) + e.get("qa", 0)) * unit if e else planned * cfg.get("remaining_share_in_review", 0.4)
    return max(0.0, planned - (i.time_spent_h or 0))


def team_load(sprints, now, cfg):
    """Remaining planned hours per person across ALL running sprints, against working days left."""
    import datetime as dt
    g = cfg["status_groups"]
    ends = [dt.datetime.fromisoformat(s["endDate"].replace("Z", "+00:00")) for s in sprints if s.get("endDate")]
    if not ends:
        return []
    end = max(ends)
    left_bd = business_days(now, end, cfg) if end > now else 0
    cap = cfg.get("focus_hours_per_day", 6) * left_bd
    rows = defaultdict(lambda: {"remaining": 0.0, "keys": [], "unestimated": 0})
    seen = set()
    for i in [i for s in sprints for i in s["issues"]]:
        if i.key in seen or i.type.lower() == "subtask" or i.status_l in g["true_done"] + g["ready_to_deploy"] + g["parked"]:
            continue
        seen.add(i.key)
        rem = remaining_hours(i, cfg)
        r = rows[i.assignee]
        if rem is None:
            r["unestimated"] += 1
        else:
            r["remaining"] += rem
        r["keys"].append(i.key)
    out = []
    for who, r in rows.items():
        load = 100 * r["remaining"] / cap if cap else (100 if r["remaining"] else 0)
        out.append({"assignee": who, "remaining_h": round(r["remaining"], 1), "capacity_h": round(cap, 1),
                    "load_pct": round(load), "tickets": len(r["keys"]), "unestimated": r["unestimated"],
                    "flag": "Over capacity" if load > 120 and who != "Unassigned" else None})
    return sorted(out, key=lambda x: -x["load_pct"])


def run(snap, cfg, jira=None, recent_done=()):
    """Everything the report needs. recent_done: issues finished in the last ~60 days, for patterns."""
    sprint_issues = [i for s in snap.sprints for i in s["issues"] if i.type.lower() != "subtask"]
    seen, current = set(), []
    for i in sprint_issues + list(snap.open_issues):
        if i.key not in seen:
            seen.add(i.key)
            current.append(assess(i, snap.now, cfg))
    past = [assess(i, snap.now, cfg) for i in recent_done if i.key not in seen]
    flagged = sorted([a for a in current if a and a["flag"]], key=lambda a: -(a["ratio"] or 0))
    return {
        "flagged": flagged,
        "patterns": assignee_patterns([a for a in current + past if a]),
        "idle": time_without_progress(sprint_issues, snap.now, cfg, jira),
        "load": {"All running sprints": team_load(snap.sprints, snap.now, cfg)},
        "coverage": {"tickets": len([a for a in current if a]),
                     "with_estimate": len([a for a in current if a and a["planned"]]),
                     "hours_logged": round(sum(a["spent"] for a in current if a), 1)},
        "past_checked": len([a for a in past if a]),
    }
