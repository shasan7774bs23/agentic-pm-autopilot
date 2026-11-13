"""Reads the daily digest that GitHub Actions posted as an issue, so the morning session sees it too."""
import datetime as dt
import json
import os
import re
import subprocess

TITLE_PREFIX = "daily PM digest"   # matched as a substring


def latest_digest(repo, label="digest"):
    """Newest digest issue (open or closed) via the gh CLI, or None. Raises RuntimeError if gh fails."""
    r = subprocess.run(["gh", "issue", "list", "-R", repo, "--label", label, "--state", "all", "--limit", "1",
                        "--json", "number,title,url,createdAt,body"], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300])
    rows = json.loads(r.stdout or "[]")
    return rows[0] if rows else None


def is_for(issue, day):
    """True when the issue title names this calendar day, e.g. '... — Wed 11 Nov 2026'."""
    return issue is not None and issue["title"].endswith(day.strftime("%a %d %b %Y"))


def action_items(body):
    """Rows of section '1. Action needed' as dicts: rule, key, summary, owner, age, detail."""
    m = re.search(r"^## 1\. Action needed\s*$(.*?)(?=^## |\Z)", body, re.M | re.S)
    out = []
    for line in (m.group(1).splitlines() if m else []):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 6 or cells[0] in ("Rule", "") or set(cells[0]) <= set("-: "):
            continue
        key = re.search(r"\[([A-Z][A-Z0-9]+-\d+)\]", cells[1])
        out.append({"rule": cells[0], "key": key.group(1) if key else cells[1], "summary": cells[2],
                    "owner": cells[3], "age": cells[4], "detail": cells[5]})
    return out


def save(body, out_dir, day):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{day:%Y-%m-%d}-digest.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path
