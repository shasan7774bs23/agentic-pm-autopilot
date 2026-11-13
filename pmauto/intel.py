"""Runs the management layer in one pass: history snapshot + backfill, forecasts, effort, risk register.
Read-only towards Jira; writes only to the local store. Each part fails on its own without stopping the rest."""
import datetime as dt

from . import effort, forecast, history, risks
from .model import fields_needed, normalize


def run(jira, store, cfg, snap, findings, versions_local, log=print):
    tz = dt.timezone(dt.timedelta(hours=cfg["timezone_offset_hours"]))
    today = snap.now.astimezone(tz).date()
    out = {"today": today, "errors": []}

    try:
        added = history.backfill(jira, store, cfg, log=log)
        out["backfilled"] = added
        log(f"history: {added} closed sprint(s) added to the velocity history")
    except Exception as ex:
        out["errors"].append(f"velocity backfill failed: {ex}")
    try:
        out["snapshot_rows"] = history.take_snapshot(store, snap, cfg, findings)
    except Exception as ex:
        out["errors"].append(f"daily snapshot failed: {ex}")

    sprint_m = {f"sprint:{s['id']}": history.sprint_metrics(s, snap.now, cfg) for s in snap.sprints}
    version_m = {f"version:{v['project']}:{v['id']}": history.version_metrics(v, cfg) for v in snap.versions}
    try:
        out["forecast"] = forecast.run(store, sprint_m, version_m, cfg, today, cfg.get("velocity_done_key", "sp_true_done"))
    except Exception as ex:
        out["errors"].append(f"forecast failed: {ex}")
        out["forecast"] = {"velocity": {}, "sprints": [], "releases": []}

    recent_done = []
    try:
        projects = ", ".join(f'"{p}"' for p in cfg["projects"])
        # Light read (no changelog): only estimates, time spent and assignee are needed for patterns.
        jql = f"project in ({projects}) AND statusCategory = Done AND resolved >= -60d"
        recent_done = [normalize(r, cfg, jira.base) for r in jira.search(jql, fields_needed(cfg))]
    except Exception as ex:
        out["errors"].append(f"recently finished tickets not read: {ex}")
    out["recent_done"] = recent_done
    try:
        out["effort"] = effort.run(snap, cfg, jira, recent_done)
    except Exception as ex:
        out["errors"].append(f"effort tracking failed: {ex}")
        out["effort"] = {"flagged": [], "patterns": [], "idle": [], "load": {}, "coverage": {}, "past_checked": 0}

    try:
        found = risks.identify(cfg, out["forecast"], out["effort"], findings, versions_local, today)
        out["risks"], out["new_risks"], out["closed_risks"] = risks.Register(store).update(found, today)
    except Exception as ex:
        out["errors"].append(f"risk register failed: {ex}")
        out["risks"], out["new_risks"], out["closed_risks"] = [], [], []
    store.audit("batch", "pm_intel", None, "failed" if out["errors"] else "ok",
                risks=len(out["risks"]), new=len(out["new_risks"]), closed=len(out["closed_risks"]), errors=out["errors"])
    return out
