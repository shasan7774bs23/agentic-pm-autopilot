"""Turns raw Jira JSON into plain facts the rules and reports work on."""
import datetime as dt
import re
from dataclasses import dataclass, field

TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%f%z"


def parse_ts(value):
    return dt.datetime.strptime(value, TS_FORMAT) if value else None


# ---------- business time ----------

def business_hours(start, end, tz_offset_hours, workdays):
    """Hours between start and end that fall on workdays, in the team's local time."""
    tz = dt.timezone(dt.timedelta(hours=tz_offset_hours))
    cur, end = start.astimezone(tz), end.astimezone(tz)
    total = 0.0
    while cur < end:
        midnight = (cur + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        nxt = min(end, midnight)
        if cur.weekday() in workdays:
            total += (nxt - cur).total_seconds() / 3600
        cur = nxt
    return total


def business_days(start, end, cfg):
    return business_hours(start, end, cfg["timezone_offset_hours"], cfg["workdays"]) / 24


def previous_workday(today, workdays):
    d = today - dt.timedelta(days=1)
    while d.weekday() not in workdays:
        d -= dt.timedelta(days=1)
    return d


# ---------- Atlassian Document Format ----------

def adf_text(node):
    if isinstance(node, list):
        return "".join(adf_text(n) for n in node)
    if not isinstance(node, dict):
        return node if isinstance(node, str) else ""
    kind = node.get("type")
    if kind == "text":
        return node.get("text", "")
    if kind == "mention":
        return node.get("attrs", {}).get("text", "@mention")
    if kind == "hardBreak":
        return "\n"
    inner = adf_text(node.get("content", []))
    return inner + "\n" if kind in ("paragraph", "heading", "listItem", "tableRow") else inner


def adf_mentions(node):
    found = []
    if isinstance(node, list):
        for n in node:
            found += adf_mentions(n)
    elif isinstance(node, dict):
        if node.get("type") == "mention":
            found.append(node.get("attrs", {}).get("id"))
        found += adf_mentions(node.get("content", []))
    return found


# ---------- estimates and client reference numbers ----------

ESTIMATE_STAGES = ("dev", "review", "qa")


def parse_estimate(text, labels):
    """Reads an estimate comment in the team's own format. `labels` maps each label the team writes to the
    stage it covers, e.g. {"DEV": "dev", "QA": "qa"} reads "DEV - 1 / QA - 0.5" as {"dev": 1.0, "qa": 0.5}.
    Returns None when the text has no estimate or no labels are configured."""
    values = {}
    for label, stage in (labels or {}).items():
        # the label and its number must stand alone: at a line start or after / | , ; and followed by the same,
        # so "QA: 2 bugs found" is not an estimate
        m = re.search(rf"(?im)(?:^|[/|,;])\s*{re.escape(label)}\s*[-:=]\s*([0-9]*\.?[0-9]+)\s*(?:[a-z]{{0,4}}\s*)?(?=$|[/|,;])",
                      text or "")
        if m:
            values[stage] = values.get(stage, 0.0) + float(m.group(1))
    return values or None


def client_ref(summary, cfg):
    """The client's own reference number in a ticket title, when the team configured a pattern for it."""
    spec = cfg.get("client_reference") or {}
    if not spec.get("pattern"):
        return None
    m = re.search(spec["pattern"], summary or "")
    return (m.group(1) if m.groups() else m.group(0)) if m else None


# ---------- normalized issue ----------

@dataclass
class Comment:
    author_id: str
    author: str
    created: dt.datetime
    text: str
    mentions: list


@dataclass
class Event:
    author_id: str
    author: str
    created: dt.datetime
    field: str
    from_value: str
    to_value: str


@dataclass
class Issue:
    key: str
    url: str
    project: str
    summary: str
    type: str
    status: str
    status_category: str
    assignee_id: str
    assignee: str
    reporter_id: str
    reporter: str
    created: dt.datetime
    updated: dt.datetime
    story_points: float
    fix_versions: list
    sprints: list
    comments: list = field(default_factory=list)
    status_since: dt.datetime = None
    estimate: dict = None
    history: list = field(default_factory=list)
    time_spent_h: float = 0.0        # Jira "Time Spent" in hours (per ticket, not per person)
    orig_estimate_h: float = None    # Jira "Original Estimate" in hours (many teams leave it empty)
    resolved: dt.datetime = None
    client_ref: str = None           # the client's reference number from the title (config "client_reference")

    @property
    def status_l(self):
        return self.status.lower()



def fields_needed(cfg):
    return ["summary", "status", "issuetype", "assignee", "reporter", "created", "updated",
            "fixVersions", "comment", "project", "timespent", "timeoriginalestimate", "resolutiondate", cfg["fields"]["story_points"], cfg["fields"]["sprint"]]


def normalize(raw, cfg, base_url):
    f = raw["fields"]
    comments = []
    for c in f.get("comment", {}).get("comments", []):
        author = c.get("author") or {}
        comments.append(Comment(author.get("accountId", ""), author.get("displayName", "?"),
                                parse_ts(c["created"]), adf_text(c.get("body")), adf_mentions(c.get("body"))))
    comments.sort(key=lambda c: c.created)
    created = parse_ts(f["created"])
    status_since = created
    history = []
    for h in sorted(raw.get("changelog", {}).get("histories", []), key=lambda h: h["created"]):
        who = h.get("author") or {}
        for item in h.get("items", []):
            history.append(Event(who.get("accountId", ""), who.get("displayName", "?"), parse_ts(h["created"]),
                                 item.get("field", ""), item.get("fromString") or "", item.get("toString") or ""))
        if any(item.get("field") == "status" for item in h.get("items", [])):
            status_since = parse_ts(h["created"])
    estimate = None
    for c in comments:
        e = parse_estimate(c.text, cfg.get("estimate_comment_labels"))
        if e:
            estimate = e
    assignee = f.get("assignee") or {}
    reporter = f.get("reporter") or {}
    return Issue(
        key=raw["key"], url=f"{base_url}/browse/{raw['key']}", project=f["project"]["key"],
        summary=f.get("summary", ""), type=f["issuetype"]["name"], status=f["status"]["name"],
        status_category=f["status"]["statusCategory"]["key"],
        assignee_id=assignee.get("accountId", ""), assignee=assignee.get("displayName", "Unassigned"),
        reporter_id=reporter.get("accountId", ""), reporter=reporter.get("displayName", "?"),
        created=created, updated=parse_ts(f["updated"]),
        story_points=f.get(cfg["fields"]["story_points"]),
        fix_versions=[v["name"] for v in f.get("fixVersions") or []],
        sprints=[{"id": s["id"], "name": s["name"], "state": s.get("state")} for s in f.get(cfg["fields"]["sprint"]) or []],
        comments=comments, status_since=status_since, estimate=estimate, history=history,
        time_spent_h=(f.get("timespent") or 0) / 3600,
        orig_estimate_h=(f["timeoriginalestimate"] / 3600) if f.get("timeoriginalestimate") else None,
        resolved=parse_ts(f.get("resolutiondate")),
        client_ref=client_ref(f.get("summary", ""), cfg),
    )


def fetch_issues(jira, jql, cfg):
    """Search, then top up any issue whose comments or changelog were truncated by the search API."""
    raws = jira.search(jql, fields_needed(cfg), expand="changelog")
    for raw in raws:
        com = raw["fields"].get("comment") or {}
        if com.get("total", 0) > len(com.get("comments", [])):
            com["comments"] = jira.all_comments(raw["key"])
        ch = raw.get("changelog") or {}
        if ch.get("total", 0) > len(ch.get("histories", [])):
            raw["changelog"] = {"histories": jira.all_changelog(raw["key"])}
    return [normalize(r, cfg, jira.base) for r in raws]
