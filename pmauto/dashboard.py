"""Creates (or updates) PRIVATE saved filters and a PRIVATE Jira dashboard owned by the signed-in user."""
from .jira import JiraError
from .settings import statuses


def _q(names):
    return ", ".join('"' + n.replace('"', '\\"') + '"' for n in names)


def filter_specs(cfg):
    """Saved filters built from the team's own status names; a filter whose statuses are not configured is left out."""
    p = ", ".join(f'"{x}"' for x in cfg["projects"])
    c = ", ".join(f'"{x}"' for x in cfg["client_accounts"])
    done = statuses(cfg, "true_done")
    parked = statuses(cfg, "parked")
    rtd = statuses(cfg, "ready_to_deploy")
    idle_ok = statuses(cfg, "not_started", "true_done")
    open_ = f" AND status not in ({_q(done)})" if done else " AND statusCategory != Done"
    specs = [
        ("Active sprints", f"project in ({p}) AND sprint in openSprints() ORDER BY status, assignee"),
        ("Client tickets open", f"project in ({p}) AND reporter in ({c}){open_} ORDER BY created DESC"),
        ("Client tickets all", f"project in ({p}) AND reporter in ({c}) ORDER BY created DESC"),
        ("Waiting on client", f"project in ({p}) AND assignee in ({c}){open_}"),
    ]
    if done:
        specs.append(("True done this sprint", f"project in ({p}) AND sprint in openSprints() AND status in ({_q(done)})"))
    if parked:
        specs.append(("Parked (Jira counts as done)", f"project in ({p}) AND sprint in openSprints() AND status in ({_q(parked)})"))
    if idle_ok:
        specs.append(("No status change 3+ days", f"project in ({p}) AND status not in ({_q(idle_ok)}) "
                                                   "AND NOT status CHANGED AFTER -3d ORDER BY updated ASC"))
    if rtd:
        specs.append(("Ready to Deploy without Fix Version", f"project in ({p}) AND status in ({_q(rtd)}) AND fixVersion is EMPTY"))
    return specs


GADGETS = [  # (filter name, gadget URI, column, row, title)
    ("Client tickets open", "rest/gadgets/1.0/g/com.atlassian.jira.gadgets:filter-results-gadget/gadgets/filter-results-gadget.xml", 0, 0, "Open client tickets"),
    ("No status change 3+ days", "rest/gadgets/1.0/g/com.atlassian.jira.gadgets:filter-results-gadget/gadgets/filter-results-gadget.xml", 0, 1, "Stuck 3+ days"),
    ("Waiting on client", "rest/gadgets/1.0/g/com.atlassian.jira.gadgets:filter-results-gadget/gadgets/filter-results-gadget.xml", 0, 2, "Waiting on client"),
    ("Ready to Deploy without Fix Version", "rest/gadgets/1.0/g/com.atlassian.jira.gadgets:filter-results-gadget/gadgets/filter-results-gadget.xml", 0, 3, "Ready to Deploy, no Fix Version"),
    ("Active sprints", "rest/gadgets/1.0/g/com.atlassian.jira.gadgets:two-dimensional-stats-gadget/gadgets/two-dimensional-stats-gadget.xml", 1, 0, "Active sprints: status × assignee"),
    ("Active sprints", "rest/gadgets/1.0/g/com.atlassian.jira.gadgets:pie-chart-gadget/gadgets/piechart-gadget.xml", 1, 1, "Active sprints by status"),
    ("Client tickets all", "rest/gadgets/1.0/g/com.atlassian.jira.gadgets:created-vs-resolved-issues-chart-gadget/gadgets/createdvsresolved-gadget.xml", 1, 2, "Client tickets: created vs resolved"),
    ("Parked (Jira counts as done)", "rest/gadgets/1.0/g/com.atlassian.jira.gadgets:filter-results-gadget/gadgets/filter-results-gadget.xml", 1, 3, "Parked (not truly done)"),
]


def ensure(jira, cfg):
    me = jira.get("/rest/api/3/myself")["accountId"]
    prefix = cfg["filter_prefix"]
    filters = {}
    for name, jql in filter_specs(cfg):
        full = prefix + name
        found = jira.get("/rest/api/3/filter/search", filterName=full, accountId=me, expand="jql")["values"]
        found = [f for f in found if f["name"] == full]
        body = {"name": full, "jql": jql, "description": "Created by pm-autopilot", "sharePermissions": []}
        if found:
            f = jira.req("PUT", f"/rest/api/3/filter/{found[0]['id']}", body=body)
        else:
            f = jira.req("POST", "/rest/api/3/filter", body=body)
        filters[name] = f

    name = cfg["dashboard_name"]
    existing = [d for d in jira.get("/rest/api/3/dashboard/search", dashboardName=name, accountId=me)["values"]
                if d["name"] == name]
    if existing:
        dash = existing[0]
    else:
        dash = jira.req("POST", "/rest/api/3/dashboard", body={
            "name": name, "description": f"{cfg.get('team_name', 'Team')} delivery overview (private). Managed by pm-autopilot.",
            "sharePermissions": [], "editPermissions": []})
    current = jira.get(f"/rest/api/3/dashboard/{dash['id']}/gadget")["gadgets"]
    have = {g.get("title") for g in current}
    notes = []
    for fname, module, col, row, title in GADGETS:
        if title in have or fname not in filters:   # a filter is left out when its statuses are not configured
            continue
        try:
            g = jira.req("POST", f"/rest/api/3/dashboard/{dash['id']}/gadget", body={
                "uri": module, "position": {"column": col, "row": row}, "title": title, "color": "blue"})
        except JiraError as e:
            notes.append(f"Gadget '{title}' not created: {e}")
            continue
        fid = filters[fname]["id"]
        prefs = {"filterId": f"filter-{fid}", "projectOrFilterId": f"filter-{fid}", "num": "20",
                 "isConfigured": "true", "refresh": "15"}
        if "two-dimensional" in module:
            prefs.update({"xstattype": "statuses", "ystattype": "assignees", "sortBy": "natural", "numberToShow": "30"})
        if "pie-chart" in module:
            prefs.update({"statType": "statuses"})
        if "created-vs-resolved" in module:
            prefs.update({"periodName": "daily", "daysprevious": "30", "isCumulative": "true", "showUnresolvedTrend": "false"})
        try:
            jira.req("PUT", f"/rest/api/3/dashboard/{dash['id']}/items/{g['id']}/properties/config", body=prefs)
        except JiraError as e:
            notes.append(f"Gadget '{title}' created but not auto-configured: {e}")
    return {"dashboard_url": f"{jira.base}/jira/dashboards/{dash['id']}", "dashboard_id": dash["id"],
            "filters": {n: (f["id"], f"{jira.base}/issues/?filter={f['id']}") for n, f in filters.items()},
            "notes": notes}
