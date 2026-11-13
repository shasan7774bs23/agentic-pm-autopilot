"""Pulls everything one report run needs from Jira, using read-only calls."""
import datetime as dt
from dataclasses import dataclass, field

from .model import fetch_issues, parse_ts, previous_workday


@dataclass
class Snapshot:
    now: dt.datetime
    sprints: list                 # active sprint dicts, each with "issues"
    open_issues: list             # union of everything open we watch
    new_client_issues: list
    versions: list                # unreleased versions with their issues
    worklogs_prev_day: list       # (author_id, author, issue, hours)
    prev_day: dt.date = None
    errors: list = field(default_factory=list)


def _jql_list(values):
    return ", ".join(f'"{v}"' for v in values)


def collect(jira, cfg, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    projects = _jql_list(cfg["projects"])
    clients = _jql_list(cfg["client_accounts"])
    errors = []

    # 1. Active sprints per board
    sprints = []
    for project, board in cfg["boards"].items():
        for s in jira.paged_values(f"/rest/agile/1.0/board/{board}/sprint", state="active"):
            s["project"] = project
            sprints.append(s)
    by_key = {}
    for s in sprints:
        s["issues"] = fetch_issues(jira, f"sprint = {s['id']}", cfg)
        for i in s["issues"]:
            by_key[i.key] = i

    # 2. Open client-related tickets outside the sprints (backlog, waiting on client, client comments)
    extra = f"project in ({projects}) AND status != Done AND (reporter in ({clients}) OR assignee in ({clients}))"
    for i in fetch_issues(jira, extra, cfg):
        by_key.setdefault(i.key, i)
    updated_by = " OR ".join(f'issue in updatedBy("{a}", "-14d")' for a in cfg["client_accounts"])
    try:
        for i in fetch_issues(jira, f"project in ({projects}) AND status != Done AND ({updated_by})", cfg):
            by_key.setdefault(i.key, i)
    except Exception as e:  # updatedBy() may reject app accounts; the other queries still cover most cases
        errors.append(f"Client-comment sweep skipped: {e}")

    # 3. New client tickets in the window
    days = cfg["new_client_ticket_window_days"]
    new_client = fetch_issues(jira, f"project in ({projects}) AND reporter in ({clients}) "
                                    f"AND created >= -{days}d ORDER BY created DESC", cfg)

    # 4. Unreleased versions that carry issues
    versions = []
    for project in cfg["projects"]:
        for v in jira.paged_values(f"/rest/api/3/project/{project}/version", status="unreleased"):
            if v.get("archived"):
                continue
            v["project"] = project
            v["issues"] = fetch_issues(jira, f'fixVersion = {v["id"]}', cfg)
            if v["issues"]:
                versions.append(v)

    # 5. Worklogs for the previous workday (team local time) for the standup draft
    tz = dt.timezone(dt.timedelta(hours=cfg["timezone_offset_hours"]))
    today_local = now.astimezone(tz).date()
    prev = previous_workday(today_local, cfg["workdays"])
    start = dt.datetime.combine(prev, dt.time(0), tz)
    end = start + dt.timedelta(days=1)
    worklogs = []
    wl_issues = fetch_issues(jira, f'project in ({projects}) AND worklogDate >= "{(prev - dt.timedelta(days=1)).isoformat()}" '
                                   f'AND worklogDate <= "{(prev + dt.timedelta(days=1)).isoformat()}"', cfg)
    for i in wl_issues:
        for w in jira.worklogs(i.key, int(start.timestamp() * 1000)):
            started = parse_ts(w["started"])
            if start <= started < end:
                a = w.get("author") or {}
                worklogs.append((a.get("accountId", ""), a.get("displayName", "?"), i, w["timeSpentSeconds"] / 3600))

    return Snapshot(now=now, sprints=sprints, open_issues=list(by_key.values()), new_client_issues=new_client,
                    versions=versions, worklogs_prev_day=worklogs, prev_day=prev, errors=errors)
