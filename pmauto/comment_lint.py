"""Guard for client-facing Jira comments: plain-English business language only.

The rule: the ticket comment explains the business need, what to do and where
(in screen terms), with acceptance criteria, like a business analyst would. No code, no file names,
no technical jargon, no estimates. The technical analysis stays in the local analysis file.
"""
import re

_RULES = [
    (r"`", "code formatting (backticks)"),
    (r"\b[\w-]+\.(cs|cshtml|sql|js|ts|tsx|jsx|java|kt|go|rb|php|json|yml|yaml|config|csproj|xml|py|razor|resx|vue|html|css)\b",
     "file name"),
    (r"(?<![A-Za-z])[A-Za-z0-9_-]+[\\/][A-Za-z0-9_.-]+[\\/][A-Za-z0-9_.-]+", "file path"),
    (r"\b[a-z]+[A-Z][A-Za-z]*\b|\b[A-Z][a-z]+[A-Z][a-z]+[A-Za-z]*\b", "code-style identifier (e.g. CamelCase)"),
    (r"\b(controller|dto|mapper|handler|repository|endpoint|api|stored procedure|sproc|sql|query|queries|"
     r"database|table column|db script|schema|migration|enum|boolean|null|json|class|method|function|"
     r"git|branch|commit|merge|pull request|\.net|frontend|backend|"
     r"viewmodel|razor|regex|cache|refactor)\b", "technical term"),
    (r"^(FILES|VERDICT|QUESTIONS):", "machine-readable line"),
]
_ALLOWED = {"PDF", "iPhone", "iPad", "iOS", "macOS"}   # plus the team's own names and acronyms: config "allowed_terms"


def problems(text, allowed=(), estimate_labels=()):
    """Returns [(line_no, what, snippet)] for every rule the text breaks.
    `estimate_labels` are the team's estimate-comment labels (config "estimate_comment_labels"):
    estimates stay internal, so "DEV - 2" style lines are flagged."""
    ok = _ALLOWED | {a.upper() for a in allowed} | set(allowed)
    rules = list(_RULES)
    if estimate_labels:
        alt = "|".join(re.escape(label) for label in estimate_labels)
        # same shape parse_estimate reads: the label and number stand alone, so "QA: 2 screens" is fine
        rules.append((rf"(?i)(?:^|[/|,;])\s*({alt})\s*[-:=]\s*[0-9.]+\s*(?:[a-z]{{0,4}}\s*)?(?=$|[/|,;])",
                      "internal estimate"))
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        for pattern, what in rules:
            flags = re.M if what.startswith("machine") else (re.I if what == "technical term" else 0)
            for m in re.finditer(pattern, line, flags):
                token = m.group(0)
                if token in ok or (what.startswith("code-style") and token.upper() in ok):
                    continue
                found.append((n, what, token))
    return found
