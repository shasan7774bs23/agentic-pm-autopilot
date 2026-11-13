"""Opening pages for the PM, and closing the TEST browsers Claude Code opened.

Rule:
- Reports and diagrams open in the PM's existing browser window, as normal: show(path).
- Test/automation browsers that Claude Code starts for a task (Playwright site checks, the
  Playwright MCP browser, headless Chromium) must be closed once the task is done: close_all().

close_all() only touches automation browsers: those with a Playwright profile or automation flags.
It never closes the PM's own browser. By default it closes the ones started from THIS Claude Code
session, plus orphans whose parent process is gone. Other live sessions' test browsers are listed,
and closed only with all_sessions=True.
"""
import json
import os
import subprocess
import sys
import webbrowser

AUTOMATION_MARKERS = ("ms-playwright", "playwright_chromium", "mcp-chrome-", "--remote-debugging-pipe",
                      "--enable-automation")


def show(path):
    """Open a report or diagram in the PM's existing browser window (new tab)."""
    url = path if "://" in path else "file:///" + os.path.abspath(path).replace("\\", "/")
    webbrowser.open(url, new=2)
    print(f"opened in your browser: {url}")


def _processes():
    if os.name != "nt":
        out = subprocess.run(["ps", "-eo", "pid=,ppid=,comm=,args="], capture_output=True, text=True).stdout
        procs = {}
        for line in out.splitlines():
            parts = line.split(None, 3)
            if len(parts) >= 3:
                procs[int(parts[0])] = {"pid": int(parts[0]), "ppid": int(parts[1]), "name": parts[2],
                                        "cmd": parts[3] if len(parts) > 3 else ""}
        return procs
    ps = ("Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine | "
          "ConvertTo-Json -Compress")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout
    return {p["ProcessId"]: {"pid": p["ProcessId"], "ppid": p["ParentProcessId"], "name": p["Name"] or "",
                             "cmd": p["CommandLine"] or ""} for p in json.loads(out or "[]")}


def _claude_ancestor(pid, procs):
    seen = set()
    while pid in procs and pid not in seen:
        seen.add(pid)
        if procs[pid]["name"].lower().startswith("claude"):
            return pid
        pid = procs[pid]["ppid"]
    return None


def test_browsers():
    """Main (non-child) automation browser processes, with the Claude session that owns each."""
    procs = _processes()
    me = _claude_ancestor(os.getpid(), procs)
    found = []
    for p in procs.values():
        name, cmd = p["name"].lower(), p["cmd"]
        if not any(b in name for b in ("chrome", "msedge", "chromium", "headless_shell")):
            continue
        if "--type=" in cmd or not any(m in cmd for m in AUTOMATION_MARKERS):
            continue
        owner = _claude_ancestor(p["pid"], procs)
        orphan = p["ppid"] not in procs
        found.append({"pid": p["pid"], "owner": owner, "mine": owner == me and me is not None, "orphan": orphan})
    return found, me


def _kill_tree(pid):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        subprocess.run(["kill", "-TERM", str(pid)], capture_output=True)


def close_all(all_sessions=False):
    found, me = test_browsers()
    closed, kept = [], []
    for b in found:
        if b["mine"] or b["orphan"] or all_sessions:
            _kill_tree(b["pid"])
            closed.append(b)
        else:
            kept.append(b)
    print(f"closed {len(closed)} test browser(s) from this session"
          + (f"; {len(kept)} belong to other Claude Code sessions and were left open "
             f"(close them too with: python -m pmauto close-windows --all-sessions)" if kept else ""))
    return closed, kept


if __name__ == "__main__":
    close_all(all_sessions="--all-sessions" in sys.argv)
