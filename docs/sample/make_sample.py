"""Builds a SAMPLE overview page and executive report from made-up data, using the tool's real code.
Nothing here comes from a real Jira: a fake Jira serves an invented team ("Acme")."""
import datetime as dt
import json
import os
import random
import re
import shutil
import sys

ROOT = sys.argv[1]          # the tool's folder
OUT = sys.argv[2]           # where the sample is written
sys.path.insert(0, ROOT)
random.seed(7)

from pmauto import exec_report, intel, overview, rules, settings  # noqa: E402
from pmauto.collect import collect  # noqa: E402
from pmauto.store import Store  # noqa: E402

TZ = dt.timezone(dt.timedelta(hours=1))
NOW = dt.datetime(2026, 11, 12, 9, 30, tzinfo=TZ)
BASE = "https://acme.atlassian.net"
HOME = os.path.join(OUT, "_work")
shutil.rmtree(HOME, ignore_errors=True)

cfg = settings.load_raw(os.path.join(ROOT, "config", "example.json"))
cfg.update({"owner_name": "Alex (PM)", "analyses_dir": os.path.join(HOME, "analyses"), "reports_dir": os.path.join(HOME, "reports"),
            "mirror_root": os.path.join(HOME, "mirrors"), "project_names": {"APP": "Shop", "WEB": "Portal", "AUTH": "Login"},
            "team_name": "Acme", "digest_repo": None, "timezone_offset_hours": 1, "timezone_label": "UTC+1"})
os.makedirs(cfg["analyses_dir"], exist_ok=True)
overview.NAMES.clear(); overview.NAMES.update(cfg["project_names"]); overview.TZ_LABEL[0] = cfg["timezone_label"]
SP, SPRINT = cfg["fields"]["story_points"], cfg["fields"]["sprint"]

PEOPLE = [("dev-01", "Dana Dev"), ("dev-02", "Sam Builder"), ("dev-03", "Rae Coder"), ("qa-01", "Quinn Tester"),
          ("dev-04", "Lee Maker"), ("qa-02", "Kim Checker")]
CLIENT = ("client-0001", "Client One")
CONNECTOR = ("connector-0001", "Connector app")
TITLES = ["Export orders to CSV from the order list", "Show delivery date on the order summary",
          "Allow customers to save a second address", "Search by order number on the dashboard",
          "Send a reminder email for unpaid invoices", "Add a filter for cancelled orders",
          "Print packing slip with the gift message", "Show stock level on the product page",
          "Let managers approve refunds over 100", "Add date range to the sales report",
          "Remember the last used payment method", "Show a warning before deleting a customer",
          "Allow bulk price update for a category", "Add customer notes to the order page",
          "Fix rounding of tax on discounted items", "Show order history in the customer profile",
          "Add a 'reorder' button to past orders", "Let support staff reset a customer password",
          "Show the courier tracking link on the order", "Limit coupon use to one per customer",
          "Add a monthly summary email for managers", "Mark VIP customers in the order list",
          "Sort products by best sellers", "Allow partial refunds on an order"]
STATUS = {"To Do": "new", "In Progress": "indeterminate", "Code Review": "indeterminate", "QA": "indeterminate",
          "Awaiting Sign-off": "done", "Ready to Deploy": "indeterminate", "Done": "done"}
FLOW = ["To Do", "In Progress", "Code Review", "QA", "Awaiting Sign-off", "Ready to Deploy", "Done"]


def iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def adf(text):
    return {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}


def person(p):
    return {"accountId": p[0], "displayName": p[1]}


ISSUES, SPRINTS, CLOSED, VERSIONS = {}, {}, {}, {}
n = 100
title_i = 0


def make_issue(project, sprint, status, created, status_since, reporter, assignee, sp, spent_h=0, est=None,
               comments=(), version=None, resolved=None):
    global n, title_i
    n += 1
    key = f"{project}-{n}"
    title = TITLES[title_i % len(TITLES)]
    title_i += 1
    if reporter == CLIENT or reporter == CONNECTOR:
        title += f" REQ-{1000 + n}"
    hist, t, cur = [], created, "To Do"
    for nxt in FLOW[1:FLOW.index(status) + 1]:
        t = t + (status_since - created) / max(1, FLOW.index(status))
        when = status_since if nxt == status else t
        hist.append({"author": person(assignee if assignee else PEOPLE[0]), "created": iso(when),
                     "items": [{"field": "status", "fromString": cur, "toString": nxt}]})
        cur = nxt
    coms = [{"author": person(a), "created": iso(w), "body": adf(txt)} for a, w, txt in comments]
    if est:
        coms.append({"author": person(PEOPLE[0]), "created": iso(created + dt.timedelta(hours=3)), "body": adf(est)})
    coms.sort(key=lambda c: c["created"])
    raw = {"key": key, "fields": {
        "summary": title, "status": {"name": status, "statusCategory": {"key": STATUS[status]}},
        "issuetype": {"name": random.choice(["Story", "Task", "Bug"])}, "assignee": person(assignee) if assignee else None,
        "reporter": person(reporter), "created": iso(created), "updated": iso(max(status_since, created)),
        "fixVersions": [{"name": version["name"], "id": version["id"]}] if version else [],
        "comment": {"comments": coms, "total": len(coms)}, "project": {"key": project},
        "timespent": int(spent_h * 3600) or None, "timeoriginalestimate": None,
        "resolutiondate": iso(resolved) if resolved else None, SP: sp,
        SPRINT: [{"id": sprint["id"], "name": sprint["name"], "state": sprint["state"]}] if sprint else None},
        "changelog": {"histories": hist, "total": len(hist)}}
    ISSUES[key] = (raw, sprint["id"] if sprint else None, version["id"] if version else None)
    return key


for b_i, (project, board) in enumerate(cfg["boards"].items()):
    short = cfg["project_names"][project]
    start = NOW - dt.timedelta(days=8)
    s = {"id": 100 + board, "name": f"{short} Sprint 12", "state": "active", "startDate": iso(start),
         "endDate": iso(start + dt.timedelta(days=14)), "originBoardId": board}
    SPRINTS[board] = s
    CLOSED[board] = []
    for k in range(8):
        cs = start - dt.timedelta(days=14 * (8 - k))
        CLOSED[board].append({"id": board * 1000 + k, "name": f"{short} Sprint {4 + k}", "state": "closed",
                              "startDate": iso(cs), "endDate": iso(cs + dt.timedelta(days=14)),
                              "completeDate": iso(cs + dt.timedelta(days=14)), "originBoardId": board})
    v = {"id": str(500 + board), "name": f"{short} 2.{4 + b_i}.0", "releaseDate": (NOW + dt.timedelta(days=4 + 3 * b_i)).date().isoformat(),
         "released": False, "archived": False}
    VERSIONS[project] = v
    size = {"APP": 16, "WEB": 12, "AUTH": 7}[project]
    for k in range(size):
        status = random.choices(FLOW, weights=[1, 2, 2, 2, 1, 3, 7])[0]
        created = start - dt.timedelta(days=random.randint(2, 20))
        since = NOW - dt.timedelta(days=random.choice([1, 2, 3, 6, 9, 15]), hours=random.randint(0, 8))
        since = max(since, created + dt.timedelta(days=1))
        who = random.choice(PEOPLE)
        sp = random.choice([1, 2, 3, 3, 5, 8])
        units = sp
        dev, rev, qa = round(units * 0.6, 2), round(units * 0.15, 2), round(units * 0.25, 2)
        est = f"DEV - {dev} / REV - {rev} / QA - {qa}" if random.random() < 0.8 else None
        over = random.choice([0.6, 0.9, 1.1, 1.3, 1.9])
        spent = round(units * 8 * over, 1) if status != "To Do" else 0
        rep = random.choice([CLIENT, CONNECTOR, PEOPLE[3], PEOPLE[3]])
        make_issue(project, s, status, created, since, rep, who, sp, spent, est,
                   version=v if random.random() < 0.55 else None,
                   resolved=since if status == "Done" else None)

# client tickets: new and untriaged, waiting on the team, waiting on the client
for project, days, comments, status, assignee in [
        ("APP", 1, [], "To Do", None),
        ("APP", 3, [], "To Do", None),
        ("WEB", 2, [(CLIENT, NOW - dt.timedelta(days=1, hours=3), "Can you confirm this also covers the mobile view?")], "To Do", PEOPLE[1]),
        ("AUTH", 5, [(PEOPLE[2], NOW - dt.timedelta(days=4), "Could you share an example of the email you expect?")], "To Do", CLIENT)]:
    make_issue(project, None, status, NOW - dt.timedelta(days=days), NOW - dt.timedelta(days=days), CONNECTOR,
               assignee, None, comments=comments)

DONE_RECENT = []
for k in range(14):
    project = random.choice(cfg["projects"])
    who = random.choice(PEOPLE)
    sp = random.choice([1, 2, 3, 5])
    when = NOW - dt.timedelta(days=random.randint(5, 50))
    key = make_issue(project, None, "Done", when - dt.timedelta(days=6), when, PEOPLE[3], who, sp,
                     round(sp * 8 * random.choice([0.8, 1.0, 1.2, 1.7]), 1), f"DEV - {sp * 0.7:g} / QA - {sp * 0.3:g}", resolved=when)
    DONE_RECENT.append(key)


class FakeJira:
    base = BASE

    def paged_values(self, path, **params):
        m = re.match(r"/rest/agile/1.0/board/(\d+)/sprint", path)
        if m:
            b = int(m.group(1))
            return [dict(SPRINTS[b])] if params.get("state") == "active" else [dict(s) for s in CLOSED[b]]
        m = re.match(r"/rest/api/3/project/(\w+)/version", path)
        if m:
            return [dict(VERSIONS[m.group(1)])]
        return []

    def search(self, jql, fields=None, expand=None, **kw):
        raws = [json.loads(json.dumps(r)) for r, _, _ in ISSUES.values()]
        info = {r["key"]: (sid, vid) for (r, sid, vid) in ISSUES.values()}
        m = re.match(r"sprint = (\d+)", jql)
        if m:
            return [r for r in raws if info[r["key"]][0] == int(m.group(1))]
        m = re.match(r"fixVersion = (\d+)", jql)
        if m:
            return [r for r in raws if info[r["key"]][1] == m.group(1)]
        if "worklogDate" in jql or "updatedBy" in jql:
            return []
        if "statusCategory = Done" in jql:
            return [r for r in raws if r["key"] in DONE_RECENT]
        clients = set(cfg["client_accounts"])
        if "created >= -" in jql:
            days = int(re.search(r"created >= -(\d+)d", jql).group(1))
            return [r for r in raws if r["fields"]["reporter"]["accountId"] in clients
                    and dt.datetime.strptime(r["fields"]["created"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
                    >= NOW - dt.timedelta(days=days)]
        if "reporter in" in jql:
            return [r for r in raws if r["fields"]["status"]["name"] != "Done"
                    and (r["fields"]["reporter"]["accountId"] in clients
                         or (r["fields"]["assignee"] or {}).get("accountId") in clients)]
        return []

    def get(self, path, **params):
        if path.endswith("/sprintreport"):
            committed = random.choice([28, 30, 32, 34, 36])
            done = round(committed * random.choice([0.55, 0.6, 0.65, 0.7]))
            return {"contents": {"completedIssuesEstimateSum": {"value": done},
                                 "issuesNotCompletedEstimateSum": {"value": committed - done},
                                 "puntedIssuesEstimateSum": {"value": 0},
                                 "completedIssues": [{}] * (done // 3), "issuesNotCompletedInCurrentSprint": [{}] * 3,
                                 "issueKeysAddedDuringSprint": {"X-1": True}}}
        if path.endswith("/myself"):
            return {"accountId": "pm-0001", "displayName": "Alex (PM)"}
        raise RuntimeError(f"not in the sample: {path}")

    def worklogs(self, key, since_ms):
        return []


jira = FakeJira()
store = Store(os.path.join(cfg["analyses_dir"], "pm.sqlite"))
snap = collect(jira, cfg, now=NOW)
findings = rules.run_all(snap.open_issues, [i for s in snap.sprints for i in s["issues"]], snap.now, cfg)
new_keys = [i.key for i in snap.new_client_issues]
for k in new_keys[:2]:
    store.add_work("ticket", k, "fp-" + k, "new client ticket")
# two analysed tickets with comment drafts waiting for the PM's "post it"
for k in new_keys[2:4]:
    store.mark_analysed(k, "h-" + k, verdict="NEEDS CLARIFICATION")
    with open(os.path.join(cfg["analyses_dir"], f"{k}-comment.md"), "w", encoding="utf-8") as fh:
        fh.write("## What the client needs\nA clear answer on which screens are in scope.\n")
local = {"mirrors": {p: {"ok": True, "head": h} for p, h in zip(cfg["projects"], ["4f2a91c", "b7d03e5", "19c6aa0"])},
         "versions": [
             {"project": "APP", "branch": "main", "code_version": "2.4.0", "site": "https://shop.example.com", "site_version": "2.4.0",
              "match": True, "release_ref": None},
             {"project": "WEB", "branch": "main", "code_version": "2.5.0", "site": "https://portal.example.com", "site_version": "2.5.0",
              "match": True, "release_ref": "release/2.5.0", "release_only_commits": 3, "release_only_tickets": ["WEB-131", "WEB-133"]}],
         "work": store.open_work()}
out = intel.run(jira, store, cfg, snap, findings, local["versions"], log=lambda *a: None)
my_id = "pm-0001"
open_refs = {w["ref"] for w in local["work"]}
drafts = overview.drafts_waiting(store, cfg["analyses_dir"], open_refs, overview.make_is_posted(store, snap, cfg["analyses_dir"], my_id))
exec_path = exec_report.write(exec_report.build(snap, findings, out, cfg, local, drafts), cfg, snap.now)
page = overview.build(snap, findings, cfg, store, overview.github_state(None), local, now=NOW, my_id=my_id, intel=out, exec_path=exec_path)
ov_path = overview.write(page, cfg, NOW)
print("errors:", out["errors"])
print(ov_path)
print(exec_path)
