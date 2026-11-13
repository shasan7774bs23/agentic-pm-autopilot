"""Setting up a machine, once.

  python -m pmauto setup                     check everything; on a new machine also create ~/.pmauto
  python -m pmauto discover --projects A,B   read Jira (read-only) and print what the interview can pre-fill
  python -m pmauto init --answers a.json     write the config from the interview answers

The interview itself is run by Claude Code (skill `pm-setup`): it calls `discover`, asks the person only what
Jira cannot tell, writes the answers file and calls `init`. Secrets never pass through the chat: the person
types them into ~/.pmauto/credentials.env themselves.

Setup only creates things in its own folders (~/.pmauto, the analyses/reports folders, the bare repo mirrors,
tools/node_modules). It never touches a developer's working clone of a product repo.
"""
import collections
import json
import os
import re
import shutil
import subprocess
import sys

from . import settings
from .mirrors import GIT_CRED

CRED_TEMPLATE = """# pm-autopilot credentials for this machine. Never commit or share this file.
# Jira API token: https://id.atlassian.com/manage-profile/security/api-tokens
JIRA_BASE_URL=https://your-site.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=
"""

# Keys that describe the TEAM's projects (shareable inside the team, no secrets) ...
TEAM_KEYS = ("team_name", "projects", "project_names", "boards", "client_accounts", "fields", "status_groups",
             "timezone_offset_hours", "timezone_label", "workdays", "code_repos", "live_sites", "login",
             "version_pattern", "live_account", "allowed_terms", "meetings", "digest_repo", "digest_cron_utc",
             "hours_per_story_point", "estimate_unit_hours", "focus_hours_per_day", "velocity_done_key",
             "thresholds_business_days", "new_client_ticket_window_days", "action_window_days",
             "bot_name_markers", "report_title", "dashboard_name", "filter_prefix", "internal_comment_group", "models",
             "estimate_comment_labels", "client_reference", "clarification_sprint", "delivery_sprint_pattern")
# ... and keys that belong to one person on one machine.
MACHINE_KEYS = ("owner_name", "notify_login", "analyses_dir", "reports_dir", "mirror_root", "test_accounts_file",
                "project_notes")
REQUIRED = ("projects", "boards", "fields", "status_groups", "timezone_offset_hours", "workdays", "client_accounts")


def _run(cmd, **kw):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=kw.pop("timeout", 900), **kw)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(cmd, 1, "", str(e))


def ensure_home():
    """Creates ~/.pmauto and a credentials template (never overwrites). Returns the created paths."""
    made = []
    os.makedirs(settings.home(), exist_ok=True)
    cred = settings.credentials_path()
    if not os.path.exists(cred) and not os.environ.get("PMAUTO_CREDENTIALS"):
        with open(cred, "w", encoding="utf-8") as fh:
            fh.write(CRED_TEMPLATE)
        made.append(cred)
    return made


def run_setup(root, cfg, args):
    results = []

    def check(name, ok, fix="", optional=False):
        results.append((name, ok, optional))
        tag = "PASS" if ok else ("SKIP" if optional else "FIX ")
        print(f"[{tag}] {name}" + ("" if ok else f"\n        -> {fix}"))

    for path in ensure_home():
        print(f"created {path} - open it and fill in your Jira site, email and API token")

    # 1. software
    check(f"Python {sys.version_info.major}.{sys.version_info.minor} (3.11+ needed)", sys.version_info >= (3, 11),
          "install Python 3.11 or newer")
    for tool, fix in (("git", "install Git"), ("node", "install Node.js 20+"), ("npm", "install Node.js 20+"),
                      ("claude", "npm install -g @anthropic-ai/claude-code")):
        check(f"{tool} installed", shutil.which(tool) is not None, fix)

    # 2. Jira credentials
    from .jira import Jira, load_credentials
    try:
        load_credentials()
        me = Jira.from_env().get("/rest/api/3/myself")
        check(f"Jira sign-in ({me.get('displayName')})", True)
    except BaseException as e:   # load_credentials raises SystemExit when missing
        check("Jira sign-in", False, f"fill in {settings.credentials_path()} (JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN). "
              f"Error: {str(e)[:160]}")

    # 3. this machine's config
    if cfg is None:
        check("config", False, f"no {settings.config_path()} yet: open Claude Code in {root} and say 'pm setup' "
              "(it asks a few questions once, for all your projects)")
        return _summary(results)
    check(f"config ({cfg['_config_path']}" + (f", team config {cfg['_team_config']}" if cfg.get("_team_config") else "")
          + ")", True)
    for key in ("analyses_dir", "reports_dir", "mirror_root"):
        os.makedirs(cfg[key], exist_ok=True)
    errors, warnings = settings.problems(cfg)
    check("config values", not errors, "; ".join(errors))
    for w in warnings:
        check("optional setting", False, w + " (say 'pm setup' to add it)", optional=True)

    # 4. GitHub (only needed for the scheduled digest)
    if cfg.get("digest_repo"):
        check("GitHub CLI installed", shutil.which("gh") is not None, "install GitHub CLI, then: gh auth login")
        gh = _run(["gh", "repo", "view", cfg["digest_repo"], "--json", "name"])
        check(f"GitHub can read {cfg['digest_repo']}", gh.returncode == 0, "gh auth login with an account that can read it")
    else:
        check("GitHub digest", False, "not configured (optional): set digest_repo to a PRIVATE repo running the "
              "daily workflow (see examples/private-layer)", optional=True)

    # 5. code mirrors (bare, separate from anyone's working clone)
    for project, spec in cfg.get("code_repos", {}).items():
        repo = spec["repo"]
        if not os.path.isdir(repo):
            r = _run(["git", *GIT_CRED, "clone", "--mirror", "--quiet", spec["remote"], repo])
            check(f"mirror {project} cloned", r.returncode == 0, f"needs read access to {spec['remote']}: {r.stderr[-200:]}")
        else:
            check(f"mirror {project} present", True)
    if not cfg.get("code_repos"):
        check("code repos", False, "none configured: ticket analysis will not read code", optional=True)

    # 6. live sites: roster + browser
    if cfg.get("live_sites"):
        acc = cfg.get("test_accounts_file")
        ok = bool(acc) and os.path.exists(acc)
        if ok:
            with open(acc, encoding="utf-8") as fh:
                ok = cfg.get("live_account") in json.load(fh).get("accounts", {})
        check("test-account roster", ok, f"set test_accounts_file in {settings.config_path()} to your roster JSON "
              f"(must contain the account '{cfg.get('live_account')}'); it holds passwords, keep it outside any repo")
        if not args.skip_browser:
            tools = os.path.join(root, "tools")
            npm = shutil.which("npm") or "npm"
            r = _run([npm, "install", "--no-audit", "--no-fund", "--silent"], cwd=tools)
            check("playwright-core installed", r.returncode == 0, r.stderr[-200:])
            npx = shutil.which("npx") or "npx"
            r = _run([npx, "playwright-core", "install", "chromium"], cwd=tools)
            check("Chromium for site checks", r.returncode == 0, r.stderr[-200:])
    else:
        check("live sites", False, "none configured: no live-site checks or version comparison", optional=True)

    # 7. project notes for the analyst skill
    notes = cfg.get("project_notes")
    check("project notes", bool(notes) and os.path.exists(notes),
          "optional: a markdown file with your projects' domain notes, roles and rules (project_notes)", optional=True)
    check("skills present", all(os.path.exists(os.path.join(root, ".claude", "skills", s, "SKILL.md"))
                                for s in ("ticket-analyst", "pm-setup")), "re-clone the repo")
    return _summary(results)


def _summary(results):
    failed = [n for n, ok, opt in results if not ok and not opt]
    print(f"\n{sum(1 for _, ok, _ in results if ok)} passed, {len(failed)} to fix, "
          f"{sum(1 for _, ok, opt in results if not ok and opt)} optional skipped."
          + (" Fix the items above and run setup again." if failed else
             " Ready: open Claude Code in this folder and say 'startpm'."))
    return 1 if failed else 0


# ---------------- discovery: what Jira can tell us ----------------

def _status_guess(name, category):
    n = name.lower()
    if category == "done" and n in ("done", "closed", "resolved", "released"):
        return "true_done"
    if "ready" in n and ("deploy" in n or "release" in n):
        return "ready_to_deploy"
    if "review" in n and "qa" not in n:
        return "review"
    if "qa" in n or "test" in n or "verif" in n:
        return "qa"
    if category == "new" or n in ("to do", "open", "backlog", "selected for development"):
        return "not_started"
    if category == "indeterminate":
        return "in_progress"
    return None


_EST_PART = re.compile(r"(?im)(?:^|[/|,;])\s*([A-Za-z]{2,12})\s*[-:=]\s*[0-9]*\.?[0-9]+\s*(?=$|[/|,;])")


def discover(jira, projects):
    """Everything setup can learn from Jira, read-only. The person confirms it in the interview."""
    from .model import adf_text
    out = {"me": {}, "projects": {}, "fields": {}, "people": {}}
    me = jira.get("/rest/api/3/myself")
    out["me"] = {"name": me.get("displayName"), "accountId": me.get("accountId"), "timeZone": me.get("timeZone")}
    fields = jira.get("/rest/api/3/field")
    out["fields"] = {
        "story_points_candidates": [{"id": f["id"], "name": f["name"]} for f in fields
                                    if "story point" in f["name"].lower() and f["id"].startswith("customfield")],
        "sprint_candidates": [{"id": f["id"], "name": f["name"]} for f in fields
                              if (f.get("schema") or {}).get("custom") == "com.pyxis.greenhopper.jira:gh-sprint"],
    }
    jql_p = ", ".join(f'"{p}"' for p in projects)
    for p in projects:
        info = {"name": jira.get(f"/rest/api/3/project/{p}").get("name"), "boards": [], "statuses": {}}
        for b in jira.paged_values("/rest/agile/1.0/board", projectKeyOrId=p):
            row = {"id": b["id"], "name": b["name"], "type": b["type"]}
            try:
                cols = jira.get(f"/rest/agile/1.0/board/{b['id']}/configuration")["columnConfig"]["columns"]
                row["columns"] = [c["name"] for c in cols]
            except Exception:
                pass
            info["boards"].append(row)
        for it in jira.get(f"/rest/api/3/project/{p}/statuses"):
            for s in it["statuses"]:
                cat = s["statusCategory"]["key"]
                info["statuses"][s["name"]] = {"category": cat, "suggested_group": _status_guess(s["name"], cat)}
        out["projects"][p] = info
    sp_ids = [c["id"] for c in out["fields"]["story_points_candidates"]]
    recent = jira.search(f"project in ({jql_p}) AND created >= -120d",
                         ["reporter", "assignee", "comment", "timeoriginalestimate", *sp_ids])
    for c in out["fields"]["story_points_candidates"]:   # the field the team really fills is the one to use
        c["filled_on_recent_tickets"] = sum(1 for r in recent if r["fields"].get(c["id"]) is not None)
    reporters, assignees = collections.Counter(), collections.Counter()
    names, est_comment, est_field, labels_seen = {}, 0, 0, collections.Counter()
    for r in recent:
        f = r["fields"]
        for role, counter in (("reporter", reporters), ("assignee", assignees)):
            who = f.get(role) or {}
            if who.get("accountId"):
                counter[who["accountId"]] += 1
                names[who["accountId"]] = who.get("displayName")
        found = set()
        for c in (f.get("comment") or {}).get("comments", []):
            # a comment with two or more "LABEL - number" parts looks like an estimate, e.g. "DEV - 1 / QA - 0.5"
            parts = _EST_PART.findall(adf_text(c.get("body")) or "")
            if len({p.upper() for p in parts}) >= 2:
                found |= {p.upper() for p in parts}
        if found:
            est_comment += 1
            labels_seen.update(found)
        if f.get("timeoriginalestimate"):
            est_field += 1
    out["people"] = {
        "top_reporters": [{"accountId": a, "name": names[a], "tickets": n} for a, n in reporters.most_common(15)],
        "top_assignees": [{"accountId": a, "name": names[a], "tickets": n} for a, n in assignees.most_common(15)],
    }
    out["estimates"] = {"tickets_checked": len(recent), "with_estimate_comment": est_comment,
                        "estimate_comment_labels_seen": dict(labels_seen.most_common(8)),
                        "with_original_estimate": est_field}
    return out


# ---------------- writing the config ----------------

def write_config(answers_path):
    """answers JSON -> team config (if `team_config` names a file to write) + ~/.pmauto/config.json."""
    with open(answers_path, encoding="utf-8") as fh:
        a = json.load(fh)
    missing = [k for k in REQUIRED if k not in a]
    if missing:
        raise SystemExit(f"answers are missing: {', '.join(missing)}")
    home = os.path.expanduser("~")
    machine = {"_note": "This machine's pm-autopilot settings. Not committed anywhere.",
               "analyses_dir": os.path.join(home, "PM-Automation", "analyses"),
               "reports_dir": os.path.join(home, "PM-Automation", "reports"),
               "mirror_root": os.path.join(home, "pm-mirrors")}
    machine.update({k: a[k] for k in MACHINE_KEYS if k in a})
    team = {"_note": "Team config for pm-autopilot. Names clients and systems: keep it in a PRIVATE place."}
    team.update({k: a[k] for k in TEAM_KEYS if k in a})
    lines = []
    team_path = a.get("team_config")
    os.makedirs(settings.home(), exist_ok=True)
    if team_path:
        team_path = os.path.abspath(os.path.expanduser(team_path))
        if os.path.exists(team_path) and not a.get("overwrite_team_config"):
            lines.append(f"kept existing team config {team_path} (set overwrite_team_config to replace it)")
        else:
            os.makedirs(os.path.dirname(team_path), exist_ok=True)
            with open(team_path, "w", encoding="utf-8") as fh:
                json.dump(team, fh, indent=2)
            lines.append(f"wrote team config {team_path}")
        machine["team_config"] = team_path
    else:
        machine.update({k: v for k, v in team.items() if not k.startswith("_")})
    path = settings.config_path()
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")
        lines.append(f"previous config saved as {path}.bak")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(machine, fh, indent=2)
    lines.append(f"wrote {path}")
    lines.append("next: python -m pmauto setup")
    return lines
