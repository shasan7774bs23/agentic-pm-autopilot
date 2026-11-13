"""Delivery channels: files always, GitHub issue in CI (GitHub emails the repo owner)."""
import json
import os
import urllib.request

from .render import to_html, to_markdown


def write_files(out_dir, name, doc):
    os.makedirs(out_dir, exist_ok=True)
    md, page = os.path.join(out_dir, name + ".md"), os.path.join(out_dir, name + ".html")
    with open(md, "w", encoding="utf-8") as fh:
        fh.write(to_markdown(doc))
    with open(page, "w", encoding="utf-8") as fh:
        fh.write(to_html(doc))
    return md, page


def _gh(method, path, body=None):
    req = urllib.request.Request("https://api.github.com" + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
                                          "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def github_issue(title, doc, notify_login, label="digest"):
    """Posts the digest as an issue in this private repo (GitHub emails the owner) and closes older ones."""
    repo = os.environ["GITHUB_REPOSITORY"]
    open_issues = _gh("GET", f"/repos/{repo}/issues?state=open&labels={label}&per_page=50") or []
    # GitHub's schedule can start hours late. If today's digest was already sent (a manual or
    # local trigger), a late scheduled run must not email the PM a second copy.
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule":
        for old in open_issues:
            if old["title"] == title:
                print("digest already sent today; scheduled run skipped:", old["html_url"])
                return old["html_url"]
    for old in open_issues:
        _gh("PATCH", f"/repos/{repo}/issues/{old['number']}", {"state": "closed"})
    body = to_markdown(doc)
    if len(body) > 60000:
        body = body[:60000] + "\n\n_…truncated; the full report is in the run's artifact._"
    body += "\n\n---\n" + (f"cc @{notify_login} · " if notify_login else "") + "full HTML report: see the workflow run artifact."
    issue = _gh("POST", f"/repos/{repo}/issues", {"title": title, "body": body, "labels": [label]})
    return issue["html_url"]
