"""Forecasts from history: sprint completion, release dates, and what changed since yesterday.

Every number is labelled as a FORECAST and kept apart from CONFIRMED dates (sprint end dates and
release dates set in Jira). The method is plain arithmetic on the team's own past sprints, written
out in `basis` so anyone can check it.
"""
import datetime as dt
import math
import statistics

from .model import business_days


def _ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def velocity(store, project, last_n=6):
    """Completed story points per closed sprint (Jira's sprint report), newest last."""
    rows = [m for _, m in store.history(f"velocity:{project}")][-last_n:]
    if not rows:
        return None
    done = [r["sp_completed"] for r in rows]
    committed = [r["sp_committed"] for r in rows]
    bd = [r.get("bd_length") or 10 for r in rows]
    ratio = [d / c for d, c in zip(done, committed) if c]
    recent, older = done[-3:], done[:-3]
    trend = None
    if older:
        a, b = statistics.mean(older), statistics.mean(recent)
        trend = "up" if b > a * 1.15 else "down" if b < a * 0.85 else "flat"
    return {
        "sprints": len(rows), "names": [r["name"] for r in rows], "completed": done, "committed": committed,
        "avg_completed": statistics.mean(done), "median_completed": statistics.median(done),
        "avg_committed": statistics.mean(committed),
        "completion_ratio": statistics.mean(ratio) if ratio else None,
        # median per sprint / median length: one unusual sprint (a big close-out) does not swing the rate
        "sp_per_bd": statistics.median(done) / statistics.median(bd) if bd else 0,
        "issues_per_bd": sum(r.get("issues_completed", 0) for r in rows) / sum(bd) if sum(bd) else 0,
        "trend": trend,
    }


def sprint_forecast(m, vel, done_key="sp_true_done"):
    """m = history.sprint_metrics(...). Expected story points done by the CONFIRMED end date.

    done_key: which 'done' matches how the sprint report counted completed work in the past
    (the board's last column). Velocity and progress must use the same definition."""
    total, done = m["sp_total"], m[done_key]
    left_bd = max(0.0, (m["bd_total"] or 0) - (m["bd_used"] or 0))
    pace_now = done / m["bd_used"] if m.get("bd_used") else 0
    if vel and vel["sp_per_bd"]:
        rate, basis = vel["sp_per_bd"], (f"team history: median {vel['median_completed']:g} SP completed per sprint "
                                         f"over the last {vel['sprints']} sprints ({vel['sp_per_bd']:.2f} SP per working day)")
    else:
        rate, basis = pace_now, "this sprint's own pace so far (no closed-sprint history yet)"
    expected = min(total, done + rate * left_bd)
    pct = 100 * expected / total if total else 100
    norm = 100 * vel["completion_ratio"] if vel and vel.get("completion_ratio") is not None else None
    if pct >= 85:
        status = "On track"
    elif norm is not None and pct >= 0.9 * norm:
        status = "Usual spillover"      # no worse than the team normally finishes; the chronic-spill risk covers it
    elif (norm is not None and pct >= 0.75 * norm) or (norm is None and pct >= 60):
        status = "At risk"
    else:
        status = "Off track"
    ready = m.get("sp_ready_or_done", done)
    return {"norm_pct": round(norm) if norm is not None else None,
            "ready_pct": round(100 * ready / total) if total else 100, "sp_ready": ready,"sprint": m["name"], "project": m["project"], "end": m["end"], "sp_total": total, "sp_done": done,
            "bd_left": round(left_bd, 1), "pace_now": round(pace_now, 2), "rate": round(rate, 2),
            "expected_done": round(expected, 1), "expected_pct": round(pct), "spill_sp": round(total - expected, 1),
            "status": status, "basis": basis}


def add_business_days(start, n, cfg):
    """Calendar date n working days after `start` (a date)."""
    d, left = start, math.ceil(n)
    while left > 0:
        d += dt.timedelta(days=1)
        if d.weekday() in cfg["workdays"]:
            left -= 1
    return d


def flow_rate(metrics):
    """SP and tickets that reached ready to deploy or done per working day in the running sprint(s) of a project.
    This matches what 'ready' means for a release (velocity counts Done only, so it would understate it)."""
    sp = sum(m.get("sp_reached_ready", 0) for m in metrics)
    n = sum(m.get("issues_reached_ready", 0) for m in metrics)
    bd = max((m.get("bd_used") or 0) for m in metrics) if metrics else 0
    if bd < 1 or not (sp or n):
        return None
    return {"sp_per_bd": sp / bd, "issues_per_bd": n / bd, "bd": bd,
            "label": f"this sprint's flow to ready to deploy ({sp:g} SP, {n} tickets in {bd:.1f} working days)"}


def release_forecast(vm, vel, today, cfg, flow=None):
    """vm = history.version_metrics(...). Forecast date to finish the remaining work, beside the confirmed date."""
    confirmed = dt.date.fromisoformat(vm["release_date"]) if vm.get("release_date") else None
    open_issues = vm["issues"] - vm["done"]
    if open_issues == 0:
        fc, basis = today, "every ticket is ready to deploy or done; only the deployment is left"
    elif flow and vm["sp_open"] and flow["sp_per_bd"]:
        days = vm["sp_open"] / flow["sp_per_bd"]
        fc = add_business_days(today, days, cfg)
        basis = f"{vm['sp_open']:g} open SP at {flow['label']} = {days:.1f} working days"
    elif flow and flow["issues_per_bd"]:
        days = open_issues / flow["issues_per_bd"]
        fc = add_business_days(today, days, cfg)
        basis = f"{open_issues} open tickets at {flow['label']} = {days:.1f} working days"
    elif vel and vm["sp_open"] and vel["sp_per_bd"]:
        days = vm["sp_open"] / vel["sp_per_bd"]
        fc = add_business_days(today, days, cfg)
        basis = f"{vm['sp_open']:g} open SP at the team's {vel['sp_per_bd']:.2f} SP per working day (Done) = {days:.1f} working days"
    elif vel and vel["issues_per_bd"]:
        days = open_issues / vel["issues_per_bd"]
        fc = add_business_days(today, days, cfg)
        basis = (f"{open_issues} open tickets (no story points) at the team's {vel['issues_per_bd']:.2f} tickets per "
                 f"working day = {days:.1f} working days")
    else:
        fc, basis = None, "no velocity history"
    slip = None
    if confirmed and fc:
        slip = (business_days(dt.datetime.combine(confirmed, dt.time(), dt.timezone.utc),
                              dt.datetime.combine(fc, dt.time(), dt.timezone.utc), cfg)
                if fc > confirmed else 0)
    overdue = bool(confirmed and confirmed < today)
    if open_issues == 0:
        status = "Ready (deploy pending)"
    elif overdue:
        status = "Overdue"
    elif slip:
        status = "At risk" if slip <= 3 else "Late"
    elif confirmed:
        status = "On track"
    else:
        status = "No date set"
    return {"version": vm["name"], "project": vm["project"], "confirmed": confirmed, "forecast": fc,
            "slip_bd": round(slip, 1) if slip else 0, "overdue": overdue, "open_issues": open_issues,
            "issues": vm["issues"], "in_qa": vm["in_qa"], "not_started": vm["not_started"],
            "status": status, "basis": basis}


def explain_change(prev, cur, prev_m=None, cur_m=None):
    """Why a forecast moved since the last run. Returns None when nothing material changed."""
    if not prev:
        return None
    reasons = []
    if "expected_pct" in cur:
        delta = cur["expected_pct"] - prev.get("expected_pct", cur["expected_pct"])
        if abs(delta) < 5:
            return None
        if prev_m and cur_m:
            added = sorted(set(cur_m["keys"]) - set(prev_m["keys"]))
            removed = sorted(set(prev_m["keys"]) - set(cur_m["keys"]))
            if cur_m["sp_total"] != prev_m["sp_total"]:
                reasons.append(f"scope {prev_m['sp_total']:g} → {cur_m['sp_total']:g} SP"
                               + (f" (added {', '.join(added)})" if added else "")
                               + (f" (removed {', '.join(removed)})" if removed else ""))
        if cur["sp_done"] != prev.get("sp_done"):
            reasons.append(f"done {prev.get('sp_done', 0):g} → {cur['sp_done']:g} SP")
        if cur["rate"] != prev.get("rate"):
            reasons.append(f"rate {prev.get('rate', 0):g} → {cur['rate']:g} SP/day")
        if not reasons:
            reasons.append("one more working day passed with no progress")
        return f"{prev.get('expected_pct')}% → {cur['expected_pct']}%: " + "; ".join(reasons)
    if prev.get("forecast") != (cur["forecast"].isoformat() if cur.get("forecast") else None):
        if prev_m and cur_m and prev_m["sp_open"] != cur_m["sp_open"]:
            reasons.append(f"open work {prev_m['sp_open']:g} → {cur_m['sp_open']:g} SP")
        if prev_m and cur_m and prev_m["issues"] != cur_m["issues"]:
            reasons.append(f"tickets in the release {prev_m['issues']} → {cur_m['issues']}")
        return f"{prev.get('forecast')} → {cur['forecast']}: " + ("; ".join(reasons) or "velocity history moved")
    return None


def run(store, snap_metrics, version_metrics, cfg, today, done_key="sp_true_done"):
    """Builds all forecasts, compares with the previous stored day, and stores today's.
    snap_metrics / version_metrics: {scope: metrics} for today."""
    vel = {p: velocity(store, p) for p in cfg["projects"]}
    out = {"velocity": vel, "sprints": [], "releases": []}
    day = today.isoformat()

    def previous(scope):
        rows = [(d, m) for d, m in store.history(scope) if d < day]
        return rows[-1] if rows else (None, None)

    for scope, m in snap_metrics.items():
        f = sprint_forecast(m, vel.get(m["project"]), done_key)
        _, pf = previous(f"forecast:{scope}")
        _, pm = previous(scope)
        f["change"] = explain_change(pf, f, pm, m)
        store.snapshot(day, f"forecast:{scope}", f)
        out["sprints"].append(f)
    flows = {p: flow_rate([m for m in snap_metrics.values() if m["project"] == p]) for p in cfg["projects"]}
    out["flow"] = flows
    for scope, vm in version_metrics.items():
        f = release_forecast(vm, vel.get(vm["project"]), today, cfg, flows.get(vm["project"]))
        _, pf = previous(f"forecast:{scope}")
        _, pm = previous(scope)
        f["change"] = explain_change(pf, f, pm, vm)
        store.snapshot(day, f"forecast:{scope}", f)
        out["releases"].append(f)
    return out
