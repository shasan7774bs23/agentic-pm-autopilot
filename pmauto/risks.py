"""Risk register: risks found from today's data, remembered across days.

Each risk carries what the spec asks for: description, impact, likelihood, severity, evidence,
recommended action, owner, and when it was first and last seen. A risk that stops appearing is closed
automatically (with the date), so the register shows what is new, what persists and what went away.
Rules are deterministic; nothing here posts or changes Jira.
"""
import datetime as dt
import hashlib
import json

from .overview import NAMES

SCHEMA = """
CREATE TABLE IF NOT EXISTS risks (
    id          TEXT PRIMARY KEY,
    project     TEXT,
    category    TEXT NOT NULL,
    title       TEXT NOT NULL,
    severity    TEXT NOT NULL,      -- High | Medium | Low
    likelihood  TEXT,
    impact      TEXT,
    evidence    TEXT,
    action      TEXT,
    owner       TEXT,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',   -- open | closed
    closed_on   TEXT,
    data        TEXT
);
"""

SEV_ORDER = {"High": 0, "Medium": 1, "Low": 2}


def _rid(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def risk(project, category, key, title, severity, likelihood, impact, evidence, action, owner=None):
    return {"id": _rid(project, category, key), "project": project, "category": category, "title": title,
            "severity": severity, "likelihood": likelihood, "impact": impact, "evidence": evidence,
            "action": action, "owner": owner}


def identify(cfg, fc, eff, findings, versions_local, today):
    """fc = forecast.run(...), eff = effort.run(...), findings = rules, versions_local = startpm version rows."""
    out = []
    # 1. sprint delivery
    for s in fc["sprints"]:
        p = s["project"]
        if s["status"] in ("At risk", "Off track"):
            sev = "High" if s["status"] == "Off track" else "Medium"
            norm = f" (the team usually finishes about {s['norm_pct']}%)" if s.get("norm_pct") is not None else ""
            out.append(risk(p, "Sprint delivery", s["sprint"], f"{s['sprint']} is forecast to finish {s['expected_pct']}% of its scope{norm}",
                            sev, "Likely" if sev == "High" else "Possible",
                            f"About {s['spill_sp']:g} SP is expected to roll into the next sprint; client commitments tied to it may slip.",
                            f"{s['sp_done']:g} of {s['sp_total']:g} SP done, {s['bd_left']:g} working days left. Basis: {s['basis']}.",
                            "Agree with the team which tickets are dropped or moved now, rather than at sprint end; tell the client early if one of theirs is affected."))
    # 2. velocity trend and chronic spillover
    for p, v in fc["velocity"].items():
        if not v:
            continue
        if v["completion_ratio"] is not None and v["completion_ratio"] < 0.6 and v["sprints"] >= 3:
            out.append(risk(p, "Planning", "chronic-spill", f"{NAMES.get(p, p)} sprints finish only {100 * v['completion_ratio']:.0f}% of what they commit",
                            "Medium", "Ongoing",
                            "Sprint plans are not a reliable promise; dates given to the client from them will slip.",
                            f"Last {v['sprints']} sprints: completed {', '.join(f'{x:g}' for x in v['completed'])} of committed "
                            f"{', '.join(f'{x:g}' for x in v['committed'])} SP (Jira sprint report).",
                            f"Plan the next sprint at about {v['median_completed']:.0f} SP (the median actually completed), or find what keeps "
                            "spilling (work parked, late client answers, unplanned work)."))
        if v["trend"] == "down":
            out.append(risk(p, "Velocity", "trend-down", f"{NAMES.get(p, p)} velocity is falling", "Low", "Possible",
                            "Forecast dates will move later if it continues.",
                            f"Completed SP per sprint: {', '.join(f'{x:g}' for x in v['completed'])}.",
                            "Check for leave, support load or blocked work in the last 3 sprints."))
    # 3. releases
    for r in fc["releases"]:
        p = r["project"]
        if r["status"] in ("Overdue", "Late", "At risk"):
            sev = "High" if r["status"] in ("Overdue", "Late") else "Medium"
            what = (f"planned for {r['confirmed']:%d %b} and still not released" if r["status"] == "Overdue"
                    else f"forecast {r['forecast']:%d %b} against the planned {r['confirmed']:%d %b} ({r['slip_bd']:g} working days late)")
            out.append(risk(p, "Release", r["version"], f"{r['version']} is {what}", sev, "Likely",
                            "The client may be expecting this release on the planned date.",
                            f"{r['open_issues']} of {r['issues']} tickets not Ready to Deploy ({r['in_qa']} in QA, {r['not_started']} not started). "
                            f"Basis: {r['basis']}.",
                            "Confirm the date with the team; either cut scope to what is ready or move the date and tell the client."))
    # 4. code drift between the live site and the branch the analyses read
    for v in versions_local:
        if v.get("release_only_commits"):
            out.append(risk(v["project"], "Release drift", v.get("release_ref"),
                            f"{NAMES.get(v['project'], v['project'])} live site runs {v['release_ref']}, with {v['release_only_commits']} commits not in {v['branch']}",
                            "Medium", "Certain",
                            "Fixes on the release branch can be lost at the next merge, and analyses read against the main branch can be wrong.",
                            f"Tickets on the release branch only: {', '.join(v.get('release_only_tickets') or []) or 'none named'}.",
                            "Ask the lead when the release branch is merged back into the main branch."))
        if v.get("match") is False:
            out.append(risk(v["project"], "Release drift", "mismatch", f"{NAMES.get(v['project'], v['project'])} site version differs from the code",
                            "High", "Certain", "Ticket analyses may describe code that is not what the client runs.",
                            f"Code {v.get('code_version')} vs site {v.get('site_version')}.", "Confirm which build is deployed before analysing more tickets."))
    # 5. effort
    for p in cfg["projects"]:
        sig = [a for a in eff["flagged"] if a["flag"] == "Significant overrun" and a["issue"].project == p]
        if sig:
            out.append(risk(p, "Effort", "significant-overruns",
                            f"{len(sig)} {NAMES.get(p, p)} ticket(s) used 1.5 times or more of their planned effort",
                            "Medium" if len(sig) >= 3 else "Low", "Happened",
                            "Estimates for this kind of work are too low, or scope grew after estimating; plans built on them slip.",
                            "; ".join(f"{a['issue'].key} {a['spent']:.0f} h vs {a['planned']:.0f} h ({a['issue'].assignee})" for a in sig[:8]) + ".",
                            "Pick the two largest and ask what changed (scope, rework, environment, unclear requirement); feed it back into estimating."))
    for pat in eff["patterns"]:
        out.append(risk(None, "Estimation", pat["assignee"], f"Estimates for {pat['assignee']}'s tickets often run over",
                        "Low", "Ongoing", "Plans that include this person's work are optimistic.",
                        f"{pat['over']} of {pat['of']} estimated tickets ran over: {', '.join(pat['keys'][:8])}. "
                        "Hours are per ticket, so this is the assignee's tickets, not necessarily their own hours.",
                        "A calm look at the estimates together: they may be set by someone else, or the tickets may be harder than they look. Not a performance judgement."))
    for name, rows in eff["load"].items():
        over = [r for r in rows if r["flag"]]
        if over:
            out.append(risk(None, "Capacity", "over-capacity", f"{len(over)} people have more planned work than working days left this sprint",
                            "Medium", "Likely", "Some of their tickets will not finish this sprint.",
                            "; ".join(f"{r['assignee']} {r['remaining_h']:g} h vs ~{r['capacity_h']:g} h ({r['load_pct']}%)" for r in over) + ".",
                            "Rebalance or move tickets to the next sprint now; check whether a big ticket is really one person's work."))
    for x in eff["idle"]:
        i = x["issue"]
        out.append(risk(i.project, "Effort", f"idle:{i.key}", f"{i.key}: time is being logged but the ticket is not moving",
                        "Medium", "Possible", "Possible hidden blocker or a ticket bigger than it looks.",
                        x["detail"] + f". Assignee: {i.assignee}.", "Ask the assignee what is blocking it.", owner=i.assignee))
    # 6. client-facing waits, by project
    for p in cfg["projects"]:
        unanswered = [f for f in findings if f.issue.project == p and f.rule == "Client comment unanswered"]
        if len(unanswered) >= 2:
            out.append(risk(p, "Client", "unanswered", f"{len(unanswered)} {NAMES.get(p, p)} client comments are waiting for a reply",
                            "High" if len(unanswered) >= 4 else "Medium", "Happening",
                            "The client sees silence; satisfaction and trust drop.",
                            "Tickets: " + ", ".join(f"{f.issue.key} ({f.age_days:.0f} d)" for f in unanswered[:10]) + ".",
                            "Reply today, even if only to say when an answer will come."))
        parked = [f for f in findings if f.issue.project == p and f.rule == "Parked"]
        if len(parked) >= 5:
            out.append(risk(p, "Flow", "parking", f"{len(parked)} {NAMES.get(p, p)} tickets parked for 10+ working days",
                            "Low", "Ongoing", "Finished work is not reaching the client; Jira charts count it as done.",
                            "Oldest: " + ", ".join(f"{f.issue.key} ({f.age_days:.0f} d)" for f in sorted(parked, key=lambda f: -f.age_days)[:6]) + ".",
                            "Agree with the client which of these can be released or closed."))
        stuck = [f for f in findings if f.issue.project == p and f.rule == "Stuck in status"]
        if len(stuck) >= 3:
            out.append(risk(p, "Flow", "stuck", f"{len(stuck)} {NAMES.get(p, p)} tickets have not moved for 3+ working days",
                            "Medium", "Happening", "Work in progress is piling up; sprint forecast gets worse.",
                            ", ".join(f"{f.issue.key} ({f.issue.status}, {f.age_days:.0f} d)" for f in stuck[:8]) + ".",
                            "Ask at stand-up what is blocking each one."))
    for r in out:
        r["owner"] = r["owner"] or cfg.get("owner_name", "the PM")
    out.sort(key=lambda r: (SEV_ORDER[r["severity"]], r["category"], r["title"]))
    return out


class Register:
    def __init__(self, store):
        self.db = store.db
        self.db.executescript(SCHEMA)

    def update(self, found, today):
        """Upserts today's risks and closes the ones no longer found. Returns (risks, new_ids, closed_rows)."""
        day = today.isoformat()
        open_before = {r["id"]: dict(r) for r in self.db.execute("SELECT * FROM risks WHERE status='open'")}
        known = {r["id"]: dict(r) for r in self.db.execute("SELECT id, first_seen FROM risks")}
        new = []
        with self.db:
            for r in found:
                if r["id"] not in open_before:
                    new.append(r["id"])
                first = known.get(r["id"], {}).get("first_seen") if r["id"] in open_before else day
                self.db.execute(
                    "INSERT INTO risks (id, project, category, title, severity, likelihood, impact, evidence, action, owner, "
                    "first_seen, last_seen, status, closed_on, data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'open', NULL, ?) "
                    "ON CONFLICT(id) DO UPDATE SET title=excluded.title, severity=excluded.severity, likelihood=excluded.likelihood, "
                    "impact=excluded.impact, evidence=excluded.evidence, action=excluded.action, owner=excluded.owner, "
                    "first_seen=?, last_seen=excluded.last_seen, status='open', closed_on=NULL",
                    (r["id"], r["project"], r["category"], r["title"], r["severity"], r["likelihood"], r["impact"],
                     r["evidence"], r["action"], r["owner"], first or day, day, json.dumps({}), first or day))
                r["first_seen"] = first or day
            gone = [rid for rid in open_before if rid not in {r["id"] for r in found}]
            for rid in gone:
                self.db.execute("UPDATE risks SET status='closed', closed_on=? WHERE id=?", (day, rid))
        closed = [open_before[rid] for rid in gone]
        # "new" = first seen today, so a second run on the same day still shows today's new risks
        new = [r["id"] for r in found if r["first_seen"] == day]
        return found, new, closed
