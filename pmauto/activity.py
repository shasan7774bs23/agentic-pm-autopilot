"""Who did what, from the Jira change history and comments (works even when time is logged by an app)."""
import datetime as dt
from collections import defaultdict

from .settings import STATUS_GROUPS, stage


def is_person(author_id, author, cfg):
    if not author_id or author_id in cfg["client_accounts"]:
        return False
    return not any(tag.lower() in (author or "").lower() for tag in cfg.get("bot_name_markers", []))


def actions_between(issues, start, end, cfg):
    """{author_id: {"name": str, "items": {issue_key: [action, ...]}, "issues": {key: issue}}}"""
    out = defaultdict(lambda: {"name": "", "items": defaultdict(list), "issues": {}})
    for i in issues:
        for e in i.history:
            if e.field == "status" and start <= e.created < end and is_person(e.author_id, e.author, cfg):
                rec = out[e.author_id]
                rec["name"] = e.author
                rec["items"][i.key].append(f"{e.from_value} → {e.to_value}")
                rec["issues"][i.key] = i
        for c in i.comments:
            if start <= c.created < end and is_person(c.author_id, c.author, cfg):
                rec = out[c.author_id]
                rec["name"] = c.author
                if "commented" not in rec["items"][i.key]:
                    rec["items"][i.key].append("commented")
                rec["issues"][i.key] = i
    return out


def forward_moves(issues, start, end, cfg):
    """{author_id: {"name", "moves": {key: (to_status, when, issue)}, "closeouts": n}}.
    Ready to Deploy -> Done is counted as a release close-out, not as a completed task."""
    out = defaultdict(lambda: {"name": "", "moves": {}, "closeouts": 0})
    for i in issues:
        for e in i.history:
            if (cfg.get("deploy_to_done_is_closeout", True)
                    and e.field == "status" and start <= e.created < end and is_person(e.author_id, e.author, cfg)
                    and e.from_value.lower() in cfg["status_groups"]["ready_to_deploy"]
                    and e.to_value.lower() in cfg["status_groups"]["true_done"]):
                out[e.author_id]["name"] = e.author
                out[e.author_id]["closeouts"] += 1   # release bookkeeping, not completed work
                continue
            if (e.field == "status" and start <= e.created < end and is_person(e.author_id, e.author, cfg)
                    and stage(e.to_value, cfg) > stage(e.from_value, cfg) >= 0
                    and stage(e.to_value, cfg) >= STATUS_GROUPS.index("review")):
                rec = out[e.author_id]
                rec["name"] = e.author
                prev = rec["moves"].get(i.key)
                if not prev or e.created > prev[1]:
                    rec["moves"][i.key] = (e.to_value, e.created, i)
    return out
