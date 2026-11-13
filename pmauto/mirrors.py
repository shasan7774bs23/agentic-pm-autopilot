"""Separate bare mirrors of the product repos. Only these are ever fetched.

The developers' own working clones are never touched: no fetch, no
checkout, nothing. Analysis reads code from the mirror with read-only git commands.
"""
import os
import re
import subprocess

GIT_CRED = ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]


def _git(repo, *args, timeout=600):
    return subprocess.run(["git", *GIT_CRED, "-C", repo, *args], capture_output=True, text=True, timeout=timeout)


def refresh(cfg, store=None):
    """Fetches every mirror. Returns {project: {"ok": bool, "head": sha, "error": text}}."""
    out = {}
    for project, spec in cfg["code_repos"].items():
        repo, branch = spec["repo"], spec["branch"]
        if not repo.replace("\\", "/").rstrip("/").endswith(".git"):
            raise SystemExit(f"{project}: refusing to fetch {repo} - only bare mirrors (*.git) may be fetched")
        r = _git(repo, "fetch", "--prune", "--quiet", "origin")
        head = _git(repo, "rev-parse", "--short", branch).stdout.strip()
        res = {"ok": r.returncode == 0, "head": head}
        if r.returncode:
            res["error"] = r.stderr.strip()[-300:]
        out[project] = res
        if store:
            store.audit("batch", "fetch_mirror", project, "ok" if res["ok"] else "failed", **res)
    return out


_VERSION = re.compile(r"<Version>\s*([0-9.]+)\s*</Version>")


def declared_version(cfg, project):
    """The version the analysis branch declares in its project file (e.g. 3.7.2.0)."""
    spec = cfg["code_repos"][project]
    csproj = spec.get("version_file")
    if not csproj:
        return None
    r = _git(spec["repo"], "show", f"{spec['branch']}:{csproj}")
    m = _VERSION.search(r.stdout) if r.returncode == 0 else None
    return m.group(1) if m else None


def compare_live(cfg, live):
    """live = {project: "v3.7.2.0" | None}. Returns rows with match True/False/None (unknown)."""
    rows = []
    for project in cfg["code_repos"]:
        code = declared_version(cfg, project)
        site = (live.get(project) or "").lstrip("vV") or None
        rows.append({"project": project, "branch": cfg["code_repos"][project]["branch"], "code_version": code,
                     "site": cfg.get("live_sites", {}).get(project, {}).get("url"), "site_version": site,
                     "match": (code == site) if code and site else None})
        rows[-1].update(release_drift(cfg, project, site))
    return rows


def release_drift(cfg, project, site_version):
    """The version NUMBER can match while the CODE differs: the site may run release branch v<version>
    with fixes that the analysis branch does not have. Returns the drift, or {} when there is no release ref."""
    if not site_version:
        return {}
    spec = cfg["code_repos"][project]
    repo, branch = spec["repo"], spec["branch"]
    for ref in (f"v{site_version}", f"refs/tags/v{site_version}", *(f"{p}v{site_version}" for p in spec.get("release_prefixes", []))):
        if _git(repo, "rev-parse", "--verify", "--quiet", ref).returncode == 0:
            only = _git(repo, "log", "--format=%s", f"{branch}..{ref}").stdout.splitlines()
            keys = sorted(set(re.findall(r"[A-Z][A-Z0-9]+-\d+", chr(10).join(only))))
            return {"release_ref": ref, "release_only_commits": len(only), "release_only_tickets": keys}
    return {"release_ref": None}


def newest_release(repo, pattern):
    r"""Highest-numbered branch whose whole name matches `pattern` (the repo's "release_branch_pattern").
    Every capture group holds dotted numbers; they are compared in order, so "release/(\d+(?:\.\d+)*)"
    picks release/2.10.0 over release/2.9.3. Branches that do not match in full are ignored."""
    rx = re.compile(pattern)
    r = _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    best = None
    for name in r.stdout.split():
        m = rx.fullmatch(name)
        if m:
            try:
                key = tuple(int(x) for g in m.groups() if g for x in g.split("."))
            except ValueError:
                continue
            if not key:   # a pattern without version groups: nothing to compare, so pick nothing
                continue
            if best is None or key > best[0]:
                best = (key, name)
    return best[1] if best else None


def resolve_branches(cfg):
    """For repos marked "branch": "newest-release", pick the newest branch matching "release_branch_pattern"."""
    for spec in cfg["code_repos"].values():
        if spec.get("branch") == "newest-release" and os.path.isdir(spec["repo"]):
            found = newest_release(spec["repo"], spec["release_branch_pattern"]) if spec.get("release_branch_pattern") else None
            spec["branch"] = found or spec.get("fallback_branch", "main")
            spec["resolved_from"] = "newest-release"
    return cfg
