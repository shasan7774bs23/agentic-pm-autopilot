"""Minimal Jira Cloud client (stdlib only, so it runs anywhere without pip installs)."""
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


def load_credentials():
    """Env vars first (CI), then ~/.pmauto/credentials.env (or the file named by PMAUTO_CREDENTIALS)."""
    env = {k: os.environ.get(k) for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")}
    if all(env.values()):
        return env
    from .settings import credentials_path
    path = os.path.expanduser(os.environ.get("PMAUTO_CREDENTIALS") or credentials_path())
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k in env and not env[k]:
                        env[k] = v.strip().strip('"')
    missing = [k for k, v in env.items() if not v]
    if missing:
        raise SystemExit(f"Missing Jira credentials: {', '.join(missing)}. Put them in {credentials_path()} "
                         "(python -m pmauto setup creates a template).")
    return env


class JiraError(RuntimeError):
    pass


class Jira:
    def __init__(self, base_url, email, token):
        self.base = base_url.rstrip("/")
        self._auth = "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()

    @classmethod
    def from_env(cls):
        c = load_credentials()
        return cls(c["JIRA_BASE_URL"], c["JIRA_EMAIL"], c["JIRA_API_TOKEN"])

    def req(self, method, path, params=None, body=None):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": self._auth, "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(5):
            request = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=90) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 4:
                    time.sleep(float(e.headers.get("Retry-After") or 2 ** attempt))
                    continue
                detail = e.read()[:400].decode("utf-8", "replace")
                raise JiraError(f"{method} {path} -> HTTP {e.code}: {detail}") from None
        raise JiraError(f"{method} {path} failed after retries")

    def get(self, path, **params):
        return self.req("GET", path, params=params or None)

    def search(self, jql, fields, expand=None):
        """Enhanced JQL search with token pagination."""
        issues, token = [], None
        while True:
            body = {"jql": jql, "fields": fields, "maxResults": 100}
            if expand:
                body["expand"] = expand
            if token:
                body["nextPageToken"] = token
            page = self.req("POST", "/rest/api/3/search/jql", body=body)
            issues.extend(page.get("issues", []))
            token = page.get("nextPageToken")
            if not token or page.get("isLast", True):
                return issues

    def all_comments(self, key):
        out, start = [], 0
        while True:
            page = self.get(f"/rest/api/3/issue/{key}/comment", startAt=start, maxResults=100)
            out.extend(page["comments"])
            start += len(page["comments"])
            if start >= page["total"] or not page["comments"]:
                return out

    def all_changelog(self, key):
        out, start = [], 0
        while True:
            page = self.get(f"/rest/api/3/issue/{key}/changelog", startAt=start, maxResults=100)
            out.extend(page["values"])
            start += len(page["values"])
            if page.get("isLast", True) or not page["values"]:
                return out

    def worklogs(self, key, started_after_ms):
        out, start = [], 0
        while True:
            page = self.get(f"/rest/api/3/issue/{key}/worklog", startAt=start, maxResults=1000,
                            startedAfter=started_after_ms)
            out.extend(page["worklogs"])
            start += len(page["worklogs"])
            if start >= page["total"] or not page["worklogs"]:
                return out

    def download(self, url, dest):
        """Downloads an attachment (follows the redirect to the media store)."""
        request = urllib.request.Request(url, headers={"Authorization": self._auth})
        with urllib.request.urlopen(request, timeout=120) as resp, open(dest, "wb") as fh:
            fh.write(resp.read())
        return dest

    def paged_values(self, path, **params):
        out, start = [], 0
        while True:
            page = self.get(path, startAt=start, maxResults=50, **params)
            out.extend(page["values"])
            start += len(page["values"])
            if page.get("isLast", True) or not page["values"]:
                return out


def upload_attachment(jira, key, path):
    """Attaches a local file to the ticket (Jira multipart upload). Returns the attachment record."""
    import uuid
    boundary = "pmauto" + uuid.uuid4().hex
    name = os.path.basename(path)
    with open(path, "rb") as fh:
        content = fh.read()
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"{jira.base}/rest/api/2/issue/{key}/attachments", data=body, method="POST",
                                 headers={"Authorization": jira._auth, "Accept": "application/json",
                                          "X-Atlassian-Token": "no-check",
                                          "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())[0]
    except urllib.error.HTTPError as e:
        raise JiraError(f"attach {name} to {key} -> HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}") from None
