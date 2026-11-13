"""Command line.

  python -m pmauto setup                      # check this machine (and create ~/.pmauto on first run)
  python -m pmauto discover --projects A,B    # read Jira to pre-fill the setup interview
  python -m pmauto init --answers answers.json  # write this machine's config from the interview
  python -m pmauto startpm                    # start of day: digest, mirrors, versions, work list, overview
  python -m pmauto pm                         # forecasts, effort, risk register, executive report
  python -m pmauto queue                      # add changed client tickets to the work list
  python -m pmauto context KEY [--as-of ISO] [--ref SHA]
  python -m pmauto mark KEY --file analysis.md --verdict READY
  python -m pmauto post KEY KEY-comment.md [--yes]
  python -m pmauto daily [--deliver file,github] [--out out]
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

from . import deliver, rules
from .collect import collect
from .dashboard import ensure
from .jira import Jira
from .store import Store
from .reports import build_completed_tasks, build_daily, build_planning, build_release_notes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


PATH_KEYS = ("analyses_dir", "reports_dir", "mirror_root", "test_accounts_file", "project_notes")
NO_CONFIG_NEEDED = ("setup", "discover", "init", "info")


def load_config(path=None, required=True):
    """This machine's ~/.pmauto/config.json (or PMAUTO_CONFIG), layered on the team config it names.
    PMAUTO_<KEY> env vars override path settings. See pmauto/settings.py."""
    from . import overview, settings
    from .mirrors import resolve_branches
    cfg = settings.load_raw(path)
    if cfg is None:
        if required:
            raise SystemExit(f"This machine is not set up yet ({settings.config_path()} not found). "
                             "Open Claude Code in this folder and say 'pm setup', or run: python -m pmauto setup")
        return None
    for key in PATH_KEYS:
        value = os.environ.get("PMAUTO_" + key.upper(), cfg.get(key))
        cfg[key] = os.path.expanduser(value) if value else value
    for spec in cfg.get("code_repos", {}).values():
        spec["repo"] = os.path.expanduser(spec["repo"].replace("{mirror_root}", cfg["mirror_root"]))
    overview.NAMES.clear()
    overview.NAMES.update(cfg.get("project_names") or {})
    hh, mm = (int(x) for x in str(cfg.get("digest_cron_utc") or "08:00").split(":"))
    overview.CRON_UTC = dt.time(hh, mm)
    overview.TZ_LABEL[0] = cfg.get("timezone_label") or "local"
    return resolve_branches(cfg)


def cmd_daily(args, cfg):
    jira = Jira.from_env()
    snap = collect(jira, cfg)
    sprint_issues = [i for s in snap.sprints for i in s["issues"]]
    findings = rules.run_all(snap.open_issues, sprint_issues, snap.now, cfg)
    tz = dt.timezone(dt.timedelta(hours=cfg["timezone_offset_hours"]))
    stamp = snap.now.astimezone(tz).strftime("%Y-%m-%d")
    out = os.path.join(args.out, stamp)

    daily = build_daily(snap, findings, cfg)
    files = list(deliver.write_files(out, "daily-digest", daily))
    files += deliver.write_files(out, "sprint-planning-draft", build_planning(snap, cfg))
    files += deliver.write_files(out, "work-moved-forward", build_completed_tasks(snap, cfg))
    for name, doc in build_release_notes(snap, cfg):
        files += deliver.write_files(os.path.join(out, "release-notes"), name.replace(" ", "_"), doc)
    with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump({"generated": snap.now.isoformat(), "sprints": [s["name"] for s in snap.sprints],
                   "open_issues_watched": len(snap.open_issues), "findings": len(findings),
                   "action_items": sum(f.severity == "action" for f in findings),
                   "errors": snap.errors}, fh, indent=2)

    # Counts only in logs: ticket content never goes to CI logs.
    print(f"sprints={len(snap.sprints)} watched={len(snap.open_issues)} findings={len(findings)} "
          f"actions={sum(f.severity == 'action' for f in findings)} files={len(files)} out={out}")
    channels = [c.strip() for c in args.deliver.split(",") if c.strip()]
    subject = daily.title
    if "github" in channels:
        print("github issue:", deliver.github_issue(subject, daily, os.environ.get("NOTIFY_LOGIN") or cfg.get("notify_login")))
    return 0


def _store(cfg):
    store = Store.from_config(cfg)
    store.import_state_json(os.path.join(cfg["analyses_dir"], "state.json"))  # one-off migration, idempotent
    return store


def cmd_context(args, cfg):
    from . import context
    as_of = dt.datetime.fromisoformat(args.as_of) if args.as_of else None
    ctx = context.build(Jira.from_env(), args.key, cfg, as_of)
    if args.ref:
        ctx["code"] = dict(ctx["code"], ref=args.ref)
    path = context.write(ctx, args.out or os.path.join(cfg["analyses_dir"], "context"), jira=Jira.from_env())
    print(path)
    return 0


def _client_tickets(jira, cfg, days):
    from . import context
    p = ", ".join(f'"{x}"' for x in cfg["projects"])
    c = ", ".join(f'"{x}"' for x in cfg["client_accounts"])
    raws = jira.search(f"project in ({p}) AND status != Done AND (reporter in ({c}) OR updated >= -{days}d) "
                       f"AND created >= -{days}d ORDER BY created DESC", ["summary", "reporter"])
    for r in raws:
        ctx = context.build(jira, r["key"], cfg)
        if ctx["reporter_is_client"] or any(x["author_id"] in cfg["client_accounts"] for x in ctx["comments"]):
            yield ctx


def cmd_queue(args, cfg):
    """Finds client tickets whose text or client comments changed since their last analysis,
    adds them to the work list, and prints the open list."""
    store = _store(cfg)
    added = _queue(store, cfg, args.days)
    items = store.open_work()
    for w in items:
        print(w["id"], w["kind"], w["ref"], w["reason"], w["note"] or "", sep="  ")
    print(f"{len(items)} open item(s), {added} new this run")
    return 0


def _queue(store, cfg, days):
    """Adds client tickets whose text or client comments changed since their last analysis; returns how many."""
    added = 0
    try:
        for ctx in _client_tickets(Jira.from_env(), cfg, days):
            prev = (store.ticket(ctx["key"]) or {}).get("content_hash")
            if prev != ctx["content_hash"]:
                added += store.add_work("ticket", ctx["key"], ctx["content_hash"], "new" if not prev else "changed",
                                        note=ctx["summary"][:120])
    except Exception as e:
        store.audit("batch", "build_worklist", None, "failed", error=str(e)[:400])
        raise
    store.audit("batch", "build_worklist", None, "ok", added=added)
    return added


def cmd_mark(args, cfg):
    from . import context
    ctx = context.build(Jira.from_env(), args.key, cfg)
    store = _store(cfg)
    store.mark_analysed(args.key, ctx["content_hash"], verdict=args.verdict, analysis_file=args.file or None)
    store.audit("claude", "analyse", args.key, "ok", verdict=args.verdict, file=args.file, hash=ctx["content_hash"])
    print(f"marked {args.key} at {ctx['content_hash']} ({args.verdict or 'no verdict'})")
    return 0


def _show_digest(cfg):
    """Prints today's digest action items from the GitHub issue; says so plainly when it is missing."""
    from . import digest_feed
    repo = cfg.get("digest_repo")
    if not repo:
        print("digest: no GitHub digest configured (digest_repo); skipped.")
        return
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=cfg["timezone_offset_hours"]))).date()
    try:
        issue = digest_feed.latest_digest(repo)
    except Exception as e:
        print(f"digest: could not read GitHub ({e}). Ticket work is not affected.")
        return
    if not digest_feed.is_for(issue, today):
        latest = issue["title"] if issue else "none"
        print(f"digest: today's digest is NOT on GitHub yet (latest: {latest}). "
              f"To send it now: gh workflow run daily-digest.yml -R {repo}")
        return
    path = digest_feed.save(issue["body"], os.path.join(cfg["analyses_dir"], "digests"), today)
    rows = digest_feed.action_items(issue["body"])
    print(f"digest: {issue['title']} - {len(rows)} action item(s) - {issue['url']} (saved: {path})")
    for r in rows:
        print(f"  digest  {r['rule']}  {r['key']}  {r['owner']}  {r['age']} bd  {r['detail'][:90]}")


def cmd_startpm(args, cfg):
    """Start-of-day: today's GitHub digest, refresh the repo mirrors, compare code version with the live sites, show the work list."""
    _show_digest(cfg)
    from . import mirrors
    store = _store(cfg)
    local = {"mirrors": mirrors.refresh(cfg, store), "versions": [], "work": []}
    for project, res in local["mirrors"].items():
        print(f"mirror {project}: {'ok' if res['ok'] else 'FAILED'} {res.get('head', '')} {res.get('error', '')}")
    live = {}
    if not args.no_live:
        live = _live_versions(cfg, store)
    for row in mirrors.compare_live(cfg, live):
        local["versions"].append(row)
        state = {True: "match", False: "MISMATCH - ask the PM before analysing against the site",
                 None: "not checked (no version in code or on the site)"}[row["match"]]
        print(f"version {row['project']}: {row['branch']}={row['code_version']} site={row['site_version']} -> {state}")
        if row["match"] is False:
            store.audit("batch", "version_check", row["project"], "failed", **row)
        if row.get("release_only_commits"):
            # Same version number, different code: tickets in this list are built on the site but not in the branch.
            print(f"  DRIFT {row['project']}: the site runs {row['release_ref']}, which has {row['release_only_commits']} "
                  f"commit(s) not in {row['branch']} (tickets: {', '.join(row['release_only_tickets']) or 'none named'}). "
                  f"For these tickets read {row['release_ref']}, not {row['branch']}; tell the PM.")
            store.audit("batch", "version_check", row["project"], "drift", **row)
    if not args.no_queue:
        added = _queue(store, cfg, args.days)
        print(f"queue: {added} client ticket(s) new or changed since the last run added to the work list")
    items = local["work"] = store.open_work()
    for w in items:
        print("work", w["id"], w["kind"], w["ref"], w["reason"], w["note"] or "", sep="  ")
    print(f"{len(items)} open work item(s)")
    unmarked = [w["ref"] for w in items if w["kind"] == "ticket"
                and os.path.exists(os.path.join(cfg["analyses_dir"], f"{w['ref']}-analysis.md"))]
    for key in unmarked:
        # An analysis exists but the ticket was never marked: it would be analysed twice.
        print(f"WARNING: {key} already has an analysis but was not marked done; "
              f"after review run: python -m pmauto mark {key} --verdict \"<verdict>\" --file <analysis>")
    if not args.no_page:
        _overview_page(cfg, store, local)
    return 0


def _overview_page(cfg, store, local):
    """Builds the one-screen PM overview (projects, new and client-updated tickets, GitHub, laptop) and opens it."""
    from . import overview
    from .windows import show
    print("overview: reading Jira for the project overview ...")
    try:
        snap = collect(Jira.from_env(), cfg)
        findings = rules.run_all(snap.open_issues, [i for s in snap.sprints for i in s["issues"]], snap.now, cfg)
        gh = overview.github_state(cfg.get("digest_repo"))
        jira = Jira.from_env()
        my_id = jira.get("/rest/api/3/myself").get("accountId")
        intel_out, exec_path = _management_layer(jira, store, cfg, snap, findings, local, my_id)
        path = overview.write(overview.build(snap, findings, cfg, store, gh, local, my_id=my_id,
                                             intel=intel_out, exec_path=exec_path), cfg, snap.now)
    except Exception as ex:
        store.audit("batch", "overview_page", None, "failed", error=str(ex)[:400])
        print(f"overview: FAILED ({ex}); the rest of startpm is not affected")
        return
    store.audit("batch", "overview_page", None, "ok", file=path)
    print(f"overview: {path}")
    show(path)


def _management_layer(jira, store, cfg, snap, findings, local, my_id):
    """History, forecasts, effort, risk register and the executive report. Never stops startpm."""
    from . import exec_report, intel, overview
    try:
        out = intel.run(jira, store, cfg, snap, findings, local.get("versions", []))
        open_refs = {w["ref"] for w in local.get("work", [])}
        drafts = overview.drafts_waiting(store, cfg["analyses_dir"], open_refs,
                                         overview.make_is_posted(store, snap, cfg["analyses_dir"], my_id))
        path = exec_report.write(exec_report.build(snap, findings, out, cfg, local, drafts), cfg, snap.now)
        hi = sum(1 for r in out["risks"] if r["severity"] == "High")
        print(f"management: {len(out['risks'])} open risk(s) ({hi} high, {len(out['new_risks'])} new, "
              f"{len(out['closed_risks'])} closed); executive report: {path}")
        for x in out["errors"]:
            print(f"management: NOTE {x}")
        store.audit("batch", "executive_report", None, "ok", file=path)
        return out, path
    except Exception as ex:
        store.audit("batch", "executive_report", None, "failed", error=str(ex)[:400])
        print(f"management: FAILED ({ex}); the overview is built without it")
        return None, None


def cmd_pm(args, cfg):
    """Management layer on its own: forecasts, effort, risks and the executive report (no mirrors, no queue)."""
    from .windows import show
    store = _store(cfg)
    jira = Jira.from_env()
    snap = collect(jira, cfg)
    findings = rules.run_all(snap.open_issues, [i for s in snap.sprints for i in s["issues"]], snap.now, cfg)
    my_id = jira.get("/rest/api/3/myself").get("accountId")
    local = {"mirrors": {}, "versions": [], "work": store.open_work()}
    out, path = _management_layer(jira, store, cfg, snap, findings, local, my_id)
    if path and not args.no_open:
        show(path)
    return 0 if path else 1


def _live_versions(cfg, store):
    import subprocess
    script = os.path.join(ROOT, "tools", "live_versions.js")
    sites = json.dumps({k: v["url"] for k, v in cfg.get("live_sites", {}).items()})
    if not sites or sites == "{}" or not cfg.get("test_accounts_file") or not cfg.get("live_account"):
        print("live version check skipped (no live_sites / test_accounts_file / live_account configured)")
        return {}
    env = dict(os.environ, PMAUTO_LOGIN=json.dumps(cfg.get("login") or {}), PMAUTO_VERSION_PATTERN=cfg["version_pattern"])
    r = subprocess.run(["node", script, cfg["test_accounts_file"], cfg["live_account"], sites],
                       capture_output=True, text=True, timeout=600, env=env)
    if r.returncode:
        store.audit("batch", "live_versions", None, "failed", error=r.stderr[-400:])
        print("live version check FAILED:", r.stderr.strip()[-300:])
        return {}
    out = json.loads(r.stdout.strip().splitlines()[-1])
    store.audit("batch", "live_versions", None, "ok", **out)
    return {k: (v or {}).get("version") for k, v in out.items()}


def cmd_post(args, cfg):
    """Posts an approved plain-English comment (KEY-comment.md). Dry run unless --yes.
    Refuses technical analysis files and any text with code, file names or jargon."""
    from .adf import strip_machine_block, to_adf
    from .comment_lint import problems
    if not args.file.replace("\\", "/").endswith("-comment.md"):
        print("REFUSED: only the plain-English comment file (KEY-comment.md) can be posted; "
              "the technical analysis stays local.")
        return 1
    if not os.path.basename(args.file).upper().startswith(args.key.upper() + "-"):
        print(f"REFUSED: {os.path.basename(args.file)} is not a comment for {args.key}; "
              f"the file name must start with {args.key}- so text never lands on the wrong ticket.")
        return 1
    with open(args.file, encoding="utf-8") as fh:
        text = strip_machine_block(fh.read())
    issues = problems(text, cfg.get("allowed_terms") or [], list(cfg.get("estimate_comment_labels") or {}))
    if issues:
        print(f"REFUSED: the comment is not plain business English ({len(issues)} issue(s)):")
        for n, what, token in issues[:20]:
            print(f"  line {n}: {what}: {token}")
        Store.from_config(cfg).audit("claude", "post_comment", args.key, "skipped", reason="lint", issues=len(issues))
        return 1
    images = [os.path.abspath(a) for a in args.attach]
    for img in images:
        if not os.path.isfile(img) or not img.lower().endswith((".png", ".jpg", ".jpeg")):
            print(f"REFUSED: {img} is not an existing .png/.jpg image.")
            return 1
        if not os.path.basename(img).upper().startswith((args.key.upper() + "-", args.key.upper() + "_")):
            print(f"REFUSED: image {os.path.basename(img)} does not start with {args.key}- (it may belong to another ticket).")
            return 1
    group = cfg.get("internal_comment_group")
    body = {"body": to_adf(text)}
    if images:
        # Inline images need Jira wiki markup on the v2 endpoint; the attachments are uploaded first.
        from .wiki import to_wiki
        body = {"body": to_wiki(text, images)}
    if group:
        body["visibility"] = {"type": "group", "value": group}
    audience = f"group '{group}' only" if group else "EVERYONE on the ticket, clients included"
    store = Store.from_config(cfg)
    if not args.yes:
        print(f"DRY RUN: would post {len(text)} chars to {args.key}, visible to {audience}.")
        for img in images:
            print(f"DRY RUN: would attach and show inline: {os.path.basename(img)} ({os.path.getsize(img) // 1024} KB)")
        store.audit("claude", "post_comment", args.key, "dry_run", chars=len(text), audience=audience, file=args.file)
        return 0
    prev = (store.ticket(args.key) or {}).get("posted_comment_id")
    if prev and not args.again:
        print(f"REFUSED: a comment ({prev}) was already posted on {args.key} for this analysis. Use --again to post another.")
        store.audit("claude", "post_comment", args.key, "skipped", reason="duplicate", previous=prev)
        return 1
    try:
        jira = Jira.from_env()
        if images:
            from .jira import upload_attachment
            for img in images:
                att = upload_attachment(jira, args.key, img)
                store.audit("pm", "attach_file", args.key, "ok", attachment_id=att["id"], file=img)
            r = jira.req("POST", f"/rest/api/2/issue/{args.key}/comment", body=body)
        else:
            r = jira.req("POST", f"/rest/api/3/issue/{args.key}/comment", body=body)
    except Exception as e:
        store.audit("claude", "post_comment", args.key, "failed", error=str(e)[:400])
        raise
    store.record_post(args.key, r["id"])
    store.audit("pm", "post_comment", args.key, "ok", comment_id=r["id"], chars=len(text), audience=audience,
                file=args.file)
    print(f"posted comment {r['id']} on {args.key} (visible to {audience})")
    return 0


def cmd_setup(args, cfg):
    from .setup import run_setup
    return run_setup(ROOT, cfg, args)


def cmd_info(args, cfg):
    """This machine's setup in one screen (no secrets: credentials are never part of the config)."""
    from . import settings
    if cfg is None:
        print(f"NOT SET UP: {settings.config_path()} not found. Say 'pm setup' in Claude Code, or run: python -m pmauto setup")
        return 1
    if args.json:
        print(json.dumps({k: v for k, v in cfg.items()}, indent=2, default=str))
        return 0
    print(f"PM:            {cfg['owner_name']} ({cfg.get('team_name')}), time zone {cfg['timezone_label']} "
          f"(UTC{cfg['timezone_offset_hours']:+g}), workdays {cfg['workdays']}")
    print(f"config:        {cfg['_config_path']}" + (f"  (team config {cfg['_team_config']})" if cfg.get("_team_config") else ""))
    for p in cfg["projects"]:
        repo = cfg.get("code_repos", {}).get(p, {})
        site = cfg.get("live_sites", {}).get(p, {}).get("url", "-")
        print(f"project {p:10} short name {settings.short_name(cfg, p):8} board {cfg['boards'].get(p)}  "
              f"code {repo.get('repo', '-')} @ {repo.get('branch', '-')}  site {site}")
    print(f"clients:       {', '.join(cfg['client_accounts'].values())}")
    print(f"folders:       analyses {cfg['analyses_dir']} | reports {cfg['reports_dir']} | mirrors {cfg['mirror_root']}")
    print(f"live checks:   roster {cfg.get('test_accounts_file') or '-'} | account {cfg.get('live_account') or '-'}")
    print(f"estimates:     comment labels {cfg.get('estimate_comment_labels') or '-'} | 1 unit = {cfg['estimate_unit_hours']} h")
    print(f"clarification: sprint {cfg.get('clarification_sprint') or '-'}")
    print(f"meetings:      {(cfg.get('meetings') or {}).get('series_subject', '-')}")
    print(f"digest repo:   {cfg.get('digest_repo') or '-'}")
    print(f"project notes: {cfg.get('project_notes') or '-'}")
    print(f"models:        {cfg.get('models')}")
    errors, warnings = settings.problems(cfg)
    for e in errors:
        print(f"CONFIG ERROR:  {e}")
    for w in warnings:
        print(f"note:          {w}")
    return 0


def cmd_look(args, cfg):
    """Read-only look at live-site pages (sign in, open, read text, screenshot) with this team's sign-in settings."""
    import subprocess
    site = cfg.get("live_sites", {}).get(args.project, {}).get("url") if args.project in cfg.get("live_sites", {}) else args.project
    if not site or not cfg.get("test_accounts_file"):
        raise SystemExit("needs live_sites and test_accounts_file in the config")
    env = dict(os.environ, PMAUTO_LOGIN=json.dumps(cfg.get("login") or {}))
    r = subprocess.run(["node", os.path.join(ROOT, "tools", "site_look.js"), cfg["test_accounts_file"], args.account,
                        site, args.out, *args.paths], env=env, timeout=900)
    return r.returncode


def cmd_discover(args, cfg):
    """Reads Jira (read-only) and prints what the setup interview can pre-fill, as JSON."""
    from .setup import discover
    print(json.dumps(discover(Jira.from_env(), [p.strip().upper() for p in args.projects.split(",") if p.strip()]),
                     indent=2, default=str))
    return 0


def cmd_init(args, cfg):
    """Writes ~/.pmauto/config.json (and the team config, if asked) from the interview answers."""
    from .setup import write_config
    for line in write_config(args.answers):
        print(line)
    return 0


def cmd_accounts(args, cfg):
    """Lists the test accounts by role (never prints passwords) so the right role is used per screen."""
    if not cfg.get("test_accounts_file") or not os.path.exists(cfg["test_accounts_file"]):
        print("No test-account roster configured (test_accounts_file); live-site checks are off.")
        return 0
    with open(cfg["test_accounts_file"], encoding="utf-8") as fh:
        roster = json.load(fh)
    for key, a in roster.get("accounts", {}).items():
        low = {k.lower(): v for k, v in a.items()}
        notes = low.get("notes") or []
        notes = [notes] if isinstance(notes, str) else notes
        # a note that mentions a password is never shown
        notes = " | ".join(n if not re.search(r"(?i)pass(word)?|pwd|secret|token", str(n)) else "(note hidden)" for n in notes)[:110]
        print(f"{key:28} {str(low.get('role', '?')):28} {notes}")
    return 0


def cmd_report(args, cfg):
    from .eod_report import build
    paths, rows = build(Jira.from_env(), _store(cfg), cfg)
    for p in paths:
        print(p)
    print(f"{len(rows)} ticket(s) in the report")
    return 0


def cmd_show(args, cfg):
    from .windows import show
    show(args.file)
    return 0


def cmd_close_windows(args, cfg):
    from .windows import close_all
    close_all(all_sessions=args.all_sessions)
    return 0


def cmd_dashboard(args, cfg):
    result = ensure(Jira.from_env(), cfg)
    print(json.dumps(result, indent=2))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="pmauto")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("daily")
    d.add_argument("--deliver", default="file")
    d.add_argument("--out", default=os.path.join(ROOT, "out"))
    sub.add_parser("dashboard")
    st = sub.add_parser("startpm")
    st.add_argument("--no-live", action="store_true", help="skip the live-site version check")
    st.add_argument("--no-queue", action="store_true", help="do not look for new or changed client tickets")
    st.add_argument("--no-page", action="store_true", help="do not build or open the overview page")
    st.add_argument("--days", type=int, default=30, help="how far back queue looks for client tickets")
    c = sub.add_parser("context")
    c.add_argument("key")
    c.add_argument("--as-of", help="ISO time; rebuild the ticket as it stood then")
    c.add_argument("--ref", help="git ref to analyse the code at (pilot: the commit before the fix)")
    c.add_argument("--out")
    q = sub.add_parser("queue")
    q.add_argument("--days", type=int, default=30)
    mk = sub.add_parser("mark")
    mk.add_argument("key")
    mk.add_argument("--file", default="")
    mk.add_argument("--verdict", choices=["READY", "NEEDS CLARIFICATION", "BLOCKED"])
    po = sub.add_parser("post")
    po.add_argument("key")
    po.add_argument("file")
    po.add_argument("--yes", action="store_true", help="really post (only on the PM's explicit instruction)")
    po.add_argument("--again", action="store_true", help="allow a second comment for the same analysis")
    po.add_argument("--attach", action="append", default=[],
                    help="image to attach and show inside the comment (file name must start with KEY-); repeatable")
    se = sub.add_parser("setup", help="check prerequisites and prepare this machine")
    se.add_argument("--accounts", help="path to shared-test-accounts.json")
    se.add_argument("--skip-browser", action="store_true")
    inf = sub.add_parser("info", help="show this machine's setup (projects, repos, sites, folders)")
    inf.add_argument("--json", action="store_true")
    lk = sub.add_parser("look", help="read-only screenshots of live-site pages")
    lk.add_argument("project", help="project key from live_sites (or a full site URL)")
    lk.add_argument("account", help="test-account key from the roster (see: pmauto accounts)")
    lk.add_argument("out", help="folder for screenshots")
    lk.add_argument("paths", nargs="+", help="page paths, e.g. /Orders /Home")
    di = sub.add_parser("discover", help="read Jira to pre-fill the setup interview (read-only)")
    di.add_argument("--projects", required=True, help="comma-separated Jira project keys")
    it = sub.add_parser("init", help="write this machine's config from the setup interview answers")
    it.add_argument("--answers", required=True, help="answers JSON written during 'pm setup'")
    sub.add_parser("accounts", help="list test accounts by role (no passwords)")
    sub.add_parser("report", help="end-of-day ticket report (.md + .html)")
    pm = sub.add_parser("pm", help="forecasts, effort, risk register and the executive report")
    pm.add_argument("--no-open", action="store_true")
    sh = sub.add_parser("show", help="open a report or diagram in your browser")
    sh.add_argument("file")
    cw = sub.add_parser("close-windows", help="close the test/automation browsers Claude Code opened")
    cw.add_argument("--all-sessions", action="store_true", help="also other Claude Code sessions' test browsers")
    args = p.parse_args(argv)
    cfg = load_config(required=args.cmd not in NO_CONFIG_NEEDED)
    return {"info": cmd_info, "look": cmd_look, "discover": cmd_discover, "init": cmd_init, "daily": cmd_daily, "dashboard": cmd_dashboard, "context": cmd_context, "queue": cmd_queue,
            "mark": cmd_mark, "post": cmd_post, "startpm": cmd_startpm, "setup": cmd_setup,
            "accounts": cmd_accounts, "report": cmd_report, "pm": cmd_pm,
            "show": cmd_show, "close-windows": cmd_close_windows}[args.cmd](args, cfg)


if __name__ == "__main__":
    sys.exit(main())
