"""Layer-1 checks: deterministic rules, no AI. Each returns Finding objects."""
from dataclasses import dataclass

from .model import business_days


@dataclass
class Finding:
    rule: str
    severity: str  # "action" (someone must act) or "info"
    issue: object
    age_days: float
    detail: str


def _is_client(account_id, cfg):
    return account_id in cfg["client_accounts"]


def _open(issue, cfg):
    return issue.status_l not in cfg["status_groups"]["true_done"]


def _active(issue, cfg):
    g = cfg["status_groups"]
    return issue.status_l not in g["true_done"] + g["not_started"]


def untriaged_client_tickets(issues, now, cfg):
    """Client ticket still To Do with no team comment and no estimate after N business days."""
    out = []
    limit = cfg["thresholds_business_days"]["untriaged"]
    for i in issues:
        if not _is_client(i.reporter_id, cfg) or i.status_l not in cfg["status_groups"]["not_started"]:
            continue
        team_comments = [c for c in i.comments if not _is_client(c.author_id, cfg)]
        age = business_days(i.created, now, cfg)
        if not team_comments and not i.estimate and age >= limit:
            if _recent(i.created, now, cfg):
                out.append(Finding("Untriaged client ticket", "action", i, age, "No team comment or estimate yet"))
            else:
                out.append(Finding("Old client ticket never answered", "info", i, age,
                                   f"Created {i.created:%d %b %Y}; still To Do with no team comment"))
    return out


def waiting_on_us(issues, now, cfg):
    """The newest comment is from the client and nobody on the team has replied."""
    out = []
    limit = cfg["thresholds_business_days"]["waiting_on_us"]
    for i in issues:
        if not _open(i, cfg) or not i.comments:
            continue
        last = i.comments[-1]
        if _is_client(last.author_id, cfg):
            age = business_days(last.created, now, cfg)
            if age >= limit:
                recent = _recent(last.created, now, cfg)
                out.append(Finding("Client comment unanswered" if recent else "Old client comment never answered",
                                   "action" if recent else "info", i, age,
                                   f"{last.author}: \"{_snip(last.text)}\""))
    return out


def waiting_on_client(issues, now, cfg):
    """Ticket assigned to a client person, or our last comment @mentions a client and they have not replied."""
    out = []
    for i in issues:
        if not _open(i, cfg):
            continue
        if _is_client(i.assignee_id, cfg):
            since = i.status_since
            out.append(Finding("Waiting on client", "info", i, business_days(since, now, cfg),
                               f"Assigned to {i.assignee}"))
            continue
        if i.comments:
            last = i.comments[-1]
            asked = [cfg["client_accounts"][m] for m in last.mentions if m in cfg["client_accounts"]]
            if not _is_client(last.author_id, cfg) and asked:
                out.append(Finding("Waiting on client", "info", i, business_days(last.created, now, cfg),
                                   f"{last.author} asked {', '.join(asked)}"))
    return out


def stuck(issues, now, cfg):
    """No status change for N business days while work is in flight."""
    out = []
    limit = cfg["thresholds_business_days"]["stuck"]
    for i in issues:
        if not _active(i, cfg) or _is_client(i.assignee_id, cfg) or i.status_l in cfg["status_groups"]["parked"]:
            continue
        age = business_days(i.status_since, now, cfg)
        if age >= limit:
            out.append(Finding("Stuck in status", "action", i, age, f"In '{i.status}' since {i.status_since:%d %b}"))
    return out


def parked(issues, now, cfg):
    """Tickets left in a parking status (status group "parked") for N business days."""
    out = []
    limit = cfg["thresholds_business_days"]["parked"]
    for i in issues:
        if i.status_l in cfg["status_groups"]["parked"] and not _is_client(i.assignee_id, cfg):
            age = business_days(i.status_since, now, cfg)
            if age >= limit:
                out.append(Finding("Parked", "info", i, age, f"Since {i.status_since:%d %b %Y}"))
    return out


def gate_checks(issues, now, cfg):
    out = []
    g = cfg["status_groups"]
    for i in issues:
        if i.status_l in g["ready_to_deploy"] and not i.fix_versions:
            out.append(Finding("Ready to Deploy without Fix Version", "action", i,
                               business_days(i.status_since, now, cfg), "Set the Fix Version before release"))
        if (i.status_l in g["qa"] and "qa" in (cfg.get("estimate_comment_labels") or {}).values()
                and not (i.estimate and "qa" in i.estimate)):
            out.append(Finding("In QA without a QA estimate", "info", i,
                               business_days(i.status_since, now, cfg), "No QA part in the estimate comment"))
    return out


def sprint_hygiene(sprint_issues, now, cfg):
    out = []
    for i in sprint_issues:
        if i.type.lower() == "subtask":
            continue
        if not i.assignee_id and _open(i, cfg):
            out.append(Finding("Unassigned in sprint", "info", i, business_days(i.created, now, cfg), ""))
        if i.story_points is None and _open(i, cfg):
            out.append(Finding("No story points", "info", i, business_days(i.created, now, cfg), ""))
    return out


def run_all(open_issues, sprint_issues, now, cfg):
    findings = []
    for rule in (untriaged_client_tickets, waiting_on_us, stuck, parked, gate_checks, waiting_on_client):
        findings += rule(open_issues, now, cfg)
    findings += sprint_hygiene(sprint_issues, now, cfg)
    order = {"action": 0, "info": 1}
    findings.sort(key=lambda f: (order[f.severity], f.rule, -f.age_days))
    return findings


def _recent(when, now, cfg):
    return (now - when).days <= cfg["action_window_days"]


def _snip(text, n=90):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"
