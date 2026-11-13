"""Where this machine keeps its settings, and the defaults every team config falls back to.

Per machine (never in the repo):
  ~/.pmauto/config.json       this person's settings; may point at a team config with "team_config"
  ~/.pmauto/credentials.env   JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN (never printed, never committed)
Set PMAUTO_HOME to use another folder (for example to test a clean install).

Team config (shareable, no secrets): projects, boards, status names, client accounts, code repos,
live sites. It usually lives in the team's own PRIVATE repo, because it names clients and systems.
"""
import json
import os
import re

DEFAULTS = {
    "owner_name": "the PM",
    "team_name": "Team",
    "timezone_label": "local",
    "project_names": {},
    "allowed_terms": [],
    "digest_repo": None,
    "notify_login": None,
    "digest_cron_utc": "08:00",
    "project_notes": None,
    "meetings": None,
    "live_sites": {},
    "code_repos": {},
    "test_accounts_file": None,
    "live_account": None,
    "login": {},
    "version_pattern": r"\bv(\d+\.\d+\.\d+(?:\.\d+)?)\b",
    # How the team writes estimates in a ticket comment, e.g. {"DEV": "dev", "QA": "qa"} for lines like
    # "DEV - 1 / QA - 0.5". Each label maps to the stage it covers: dev, review or qa. Empty = not used.
    "estimate_comment_labels": {},
    # Optional reference number the client puts in ticket titles: {"label": "Case", "pattern": "(\\d+)\\s*$"}.
    "client_reference": None,
    # Sprint where tickets that need the client's answers are parked, if the team has one.
    "clarification_sprint": None,
    # Optional regex a sprint name must match to count as a delivery sprint in velocity history.
    "delivery_sprint_pattern": None,
    "internal_comment_group": None,
    "new_client_ticket_window_days": 7,
    "action_window_days": 30,
    "thresholds_business_days": {"untriaged": 1, "waiting_on_us": 1, "stuck": 3, "parked": 10},
    "bot_name_markers": ["Tempo", "Automation for Jira", "Connector for", "Jira Service", "Bitbucket", "GitHub"],
    "hours_per_story_point": 8,
    # Moves from ready_to_deploy to true_done are release bookkeeping, not completed work (False counts them).
    "deploy_to_done_is_closeout": True,
    # Sprint health: 'Behind' when done % of SP trails elapsed time % by more than this many points.
    "behind_margin_points": 20,
    # Remaining work when a ticket has no estimate split by stage: share of the plan left in QA / in review.
    "remaining_share_in_qa": 0.25,
    "remaining_share_in_review": 0.4,
    "focus_hours_per_day": 6,
    "velocity_done_key": "sp_true_done",
    "models": {"analysis": "haiku", "analysis_fallback": "sonnet", "meeting_filter": "haiku", "effort": "low"},
}


# Status groups, in workflow order. Each holds the team's own Jira status names (lower case).
STATUS_GROUPS = ("not_started", "in_progress", "review", "qa", "parked", "ready_to_deploy", "true_done")


def stage(status, cfg):
    """Position of a status in the workflow (0 = not started ... 6 = done), or -1 when it is in no group.
    A status listed in several groups counts at the furthest one."""
    s = (status or "").lower()
    found = [n for n, k in enumerate(STATUS_GROUPS) if s in cfg["status_groups"].get(k, ())]
    return max(found) if found else -1


def statuses(cfg, *groups):
    """All status names (lower case) in the given groups."""
    return [s for g in groups for s in cfg["status_groups"].get(g, ())]


def problems(cfg):
    """(errors, warnings) about the config: a bad regex is an error; a missing optional setting that switches
    a feature off is a warning, so an older config does not lose features silently."""
    errors, warnings = [], []

    def regex(name, pattern, need_group=False):
        try:
            rx = re.compile(pattern)
        except re.error as e:
            errors.append(f"{name} is not a valid regex ({e}): {pattern}")
            return
        if need_group and not rx.groups:
            errors.append(f"{name} needs at least one (group) holding the version numbers: {pattern}")

    if (cfg.get("client_reference") or {}).get("pattern"):
        regex("client_reference.pattern", cfg["client_reference"]["pattern"])
    if cfg.get("delivery_sprint_pattern"):
        regex("delivery_sprint_pattern", cfg["delivery_sprint_pattern"])
    if cfg.get("version_pattern"):
        regex("version_pattern", cfg["version_pattern"], need_group=True)
    for key, spec in (cfg.get("code_repos") or {}).items():
        if spec.get("release_branch_pattern"):
            regex(f"code_repos.{key}.release_branch_pattern", spec["release_branch_pattern"], need_group=True)
    for stage in (cfg.get("estimate_comment_labels") or {}).values():
        if stage not in ("dev", "review", "qa"):
            errors.append(f"estimate_comment_labels: stage '{stage}' must be dev, review or qa")
    groups = cfg.get("status_groups") or {}
    for g in ("in_progress", "review"):
        if not groups.get(g):
            warnings.append(f"status_groups.{g} is empty: activity, effort and reopen tracking will miss those statuses")
    if not cfg.get("estimate_comment_labels"):
        warnings.append("estimate_comment_labels is empty: estimate comments are not read, and comments are not "
                        "checked for internal estimates before posting")
    return errors, warnings


def home():
    return os.path.expanduser(os.environ.get("PMAUTO_HOME") or "~/.pmauto")


def config_path():
    return os.path.join(home(), "config.json")


def credentials_path():
    return os.path.join(home(), "credentials.env")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_raw(path=None):
    """Team config + this machine's settings. Returns None when the machine is not set up yet."""
    path = path or os.environ.get("PMAUTO_CONFIG") or config_path()
    if not os.path.exists(path):
        return None
    mine = _read(path)
    cfg = dict(DEFAULTS)
    team = mine.get("team_config")
    if team:
        team = os.path.expanduser(team)
        if not os.path.isabs(team):
            team = os.path.join(os.path.dirname(os.path.abspath(path)), team)
        cfg.update({k: v for k, v in _read(team).items() if not k.startswith("_")})
        cfg["_team_config"] = team
    cfg.update({k: v for k, v in mine.items() if not k.startswith("_") and k != "team_config"})
    cfg["_config_path"] = os.path.abspath(path)
    groups = {k: [s.lower() for s in v] for k, v in (cfg.get("status_groups") or {}).items()}
    for k in STATUS_GROUPS:
        groups.setdefault(k, [])
    cfg["status_groups"] = groups
    if "estimate_unit_hours" not in cfg:
        cfg["estimate_unit_hours"] = cfg["hours_per_story_point"]
    team = cfg.get("team_name") or "Team"
    cfg.setdefault("report_title", f"{team} daily PM digest")
    cfg.setdefault("dashboard_name", f"{team} Delivery (auto)")
    cfg.setdefault("filter_prefix", f"{team} auto · ")
    return cfg


def short_name(cfg, project):
    return (cfg.get("project_names") or {}).get(project, project or "All")
