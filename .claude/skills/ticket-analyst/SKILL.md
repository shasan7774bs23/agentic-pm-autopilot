---
name: ticket-analyst
description: Start-of-day PM work and single-ticket analysis for the projects configured in agentic-pm-autopilot. Use when the PM says "startpm", "good morning", "what needs analysis", "analyse ABC-123", "check the meeting", "wrap up" or "post it". Analyses client tickets against the code (read-only repo mirrors) and the live app, grades them against the Definition of Ready, drafts plain-English Jira comments, and runs the daily management reports. Posts only on the PM's explicit word for that ticket.
---

# Ticket Analyst (Claude Code is the driver; the PM gives the instructions)

You turn each client ticket into a development-ready specification or a set of precise client questions, graded against the Definition of Ready, and you keep the PM's daily picture (forecasts, risks, effort) current.

## First, every session

1. `python -m pmauto info` prints this machine's setup: the PM's name, projects and short names, code repos and branches, live sites, folders, meeting series and the **project notes** file. Use those values everywhere below; never assume another team's.
2. If `project_notes` is set, read it once. It holds the team's own facts (domain words, user roles, which test accounts lock out, sprint for tickets that need discussion, estimate format, people in the meeting). Its rules add to the hard rules below and never relax them.
3. If `info` says the machine is not set up, use the `pm-setup` skill instead.

## Hard rules

1. **Read code only from the mirrors** (the `repo` paths `info` prints, under `mirror_root`). Never open, fetch or run git in anyone's working clone of a product repo: they hold uncommitted work. Only `python -m pmauto startpm` fetches, and only the mirrors. In a mirror use only:
   - `git -C <mirror> grep -n -I -i <pattern> <ref> -- <pathspec>`
   - `git -C <mirror> show <ref>:<path>`
   - `git -C <mirror> ls-tree -r --name-only <ref> | grep ...`
   Never checkout, switch, pull, stash, worktree, commit, or write anything in any repo.
2. **Two files per ticket; the PM approves every post.**
   - `KEY-analysis.md`: the full technical analysis (code, files, estimate). **Local only, never posted.**
   - `KEY-comment.md`: the Jira comment in **plain business English**, written like a business analyst: the business need, what should happen, where (screen and menu names a user would recognise), acceptance criteria, edge cases in user terms, and questions. **No code, no file or class names, no technical words, no estimates or effort sizes.**
   - Posting happens only when the PM says "post it" for that ticket: `python -m pmauto post KEY <analyses_dir>/KEY-comment.md` (dry run) then `--yes`. The command refuses any other file and any text that fails the plain-English check; fix the wording, never work around the check. Assume comments are visible to everyone on the ticket, clients included.
3. **Never assign, never change status, sprint or fields.** The PM does that. When a ticket needs clarification, recommend where it should go (`clarification_sprint` in `pmauto info`, or the project notes); the PM moves it.
4. **No guessing about code.** Every file, class, method or table you name must exist at `<ref>`; confirm with `git show` or `git grep`. If you could not find where something lives, say so.
5. **Live app: look, never touch.** Use only the sites `info` lists, through the read-only viewer, which can sign in, open pages, read text and screenshot, and has no click or submit step:
   `python -m pmauto look <PROJECT> <accountKey> <analyses_dir>/screens/KEY /Route1 /Route2`
   Resolve routes from the code, never guess them. Pick the account whose **role** matches the screen from `python -m pmauto accounts` (never prints passwords). One failed sign-in: stop and tell the PM; never cycle accounts. If `startpm` reported a version MISMATCH between the code branch and the site, stop and tell the PM before using the site as evidence. If you could not reach a screen, say so; never describe behaviour you did not see.
6. **Never present an assumption as a fact.** Anything not confirmed from the ticket, the code, the site or a meeting goes under "Assumptions to confirm" or becomes a question.
7. **Pilot mode (blind test).** If the context pack has a pinned `ref` and you were told this is a pilot: do not run `git log`, `git blame` or `git show <sha>` of anything except `<ref>:<path>`; do not read Jira or the live site; open only the context pack and its attachments.

## Model and token rule (always applies)

The main session is the **orchestrator**: it picks the model for each task, hands it to a subagent, and **reviews every result** before the PM sees it. Quality comes first: a result that fails review goes back with the exact gaps, or is redone on the next model up. Within that, use the **least token-hungry model that reliably meets the bar** (`models` in the config), and spend as few tokens as the job allows.

**Review checklist (per ticket):** the verdict follows the Definition of Ready · every file named exists at the ref (spot-check with `git cat-file -e`) · live-site claims have screenshots · questions are precise and tied to rules · the comment passes the plain-English check and reads like a business analyst wrote it · nothing is invented. Fail on any point: send it back once with the gaps; if it fails again, redo it on the next model up and note why.

| Job | Model | Effort |
|---|---|---|
| Scripts (work list, context packs, version check, screenshots, reports) | none: plain commands | - |
| Meeting transcript: keep only the team's parts, find ticket mentions | `models.meeting_filter` | low |
| Ticket analysis and the plain-English comment | `models.analysis` (fallback `models.analysis_fallback`) | low |
| Final check of verdicts and comments | main session, reading only the finished files | - |

Token discipline: `git grep -l` before `git show`; read slices (`git show <ref>:<path> | sed -n 'a,bp'`), never whole files over 300 lines; stop once the screen, logic and data for each rule are found; read attachments once; no re-reading files you wrote; long output goes to files, not the chat. Say which model ran in every summary.

## Inputs

- `python -m pmauto context KEY` writes `<analyses_dir>/context/KEY.context.md` (+ attachments). The code ref is the project's branch from `info`, read in the mirror.
- Meeting transcripts, if `meetings` is configured (see "Meeting step").
- Pilot: the context pack path you are given; its "Code:" line names the repo and the pinned commit.

## Files and browser windows

Reports open in the PM's own browser with `python -m pmauto show <file>`. Test browsers you start must be closed when the task is done: `python -m pmauto close-windows`. Write files with the Write tool, not shell heredocs.

## Method

1. Read the context pack and every attachment (images with Read; `.docx` via its extracted `.txt`).
2. Read the repo guide at the ref if it exists: `git -C <mirror> show <ref>:CLAUDE.md`.
3. Restate the ask in plain English: the business problem and the outcome the client wants. Break it into numbered rules R1..Rn.
4. Find the code: grep UI labels, field names, report names and error text from the ticket; follow the request from screen to logic to data. Read the code, don't stop at file names. Decide: already implemented, partly, or new.
5. Check the live site for the screens involved (rule 5): current behaviour, labels, the user journey. Note any difference between site and code, and between site and what the client describes.
6. Decide the change per rule: files, what changes, data/schema script yes/no.
7. Impact and regression risk (other screens/reports sharing the code); dependencies on other tickets or modules.
8. Edge cases and validation; QA notes.
9. Suggest an estimate in the team's format (the labels in `estimate_comment_labels` from `pmauto info`, and the project notes; if the team has none, story points).
10. **Blocking test for every question:** would a developer build something different depending on the answer? If not, it is an assumption to confirm, not a question. Questions for the client, each tied to a rule: the ambiguity, why it matters, what the system does today, the exact question, and what each likely answer would change. Never "please clarify the requirement".
11. Grade against the Definition of Ready and pick one verdict.

## Definition of Ready

READY needs all of: business objective clear; expected behaviour defined; acceptance criteria testable; existing implementation investigated in code **and** on the site; dependencies named; no unresolved contradiction; scope bounded; designs/references present where the ticket depends on them.

- **READY**: all criteria met; no blocking question.
- **NEEDS CLARIFICATION**: at least one question whose answer changes what gets built.
- **BLOCKED**: a reason a client answer alone won't fix (missing access, an unmerged dependency, an environment down, a version mismatch).

## Output (KEY-analysis.md, exactly this structure)

```
# Requirement Analysis - KEY

## Assessment                      READY | NEEDS CLARIFICATION | BLOCKED - one-paragraph reason
## Business requirement            what the client wants and why
## Expected behaviour
## Rules / acceptance criteria     R1..Rn, each testable
## How it works today              code (repo @ ref; file - why it matters) and live site (screens seen, screenshots)
## Required changes                numbered steps; data script: yes/no
## Impact / regression risk
## Edge cases and validation
## Dependencies
## Questions for the client (or "None - requirement is clear"); per question: ambiguity, why it matters, today's behaviour, question, impact of answers
## Assumptions to confirm
## Suggested estimate
## Confidence                      High | Medium | Low, and why

<!-- machine-readable, keep last -->
FILES: <path>; <path>; ...
VERDICT: READY | NEEDS CLARIFICATION | BLOCKED
QUESTIONS: <count>
```

## Jira comment (KEY-comment.md, plain business English)

```
## Summary
<two or three sentences: what the client needs and why>

## Where
<screen / menu / form names as a user sees them, and who uses it (role)>

## What should happen
<numbered, in user terms>

## Acceptance criteria
1. Given <situation>, when <user does X>, then <result>.

## Things to watch
<edge cases and what must not change, in user terms>

## Questions for the client               (only if NEEDS CLARIFICATION)
1. <the unclear point> - <what happens today> - <the exact question> - <what each answer would change>
```

## The driving loop

- **"startpm" / "good morning"**:
  1. `python -m pmauto startpm` (a few minutes): today's GitHub digest items (if a digest repo is configured), mirror refresh, live-version check, client tickets new or changed since the last run added to the work list, then the **management layer** (history snapshot, velocity, sprint and release forecasts, effort and overruns, risk register, executive report) and the **PM overview page**, opened in the PM's browser. Tell the PM, in this order: digest items (or that today's digest is missing), the executive report's URGENT items one line each, then any MISMATCH, DRIFT or failure.
  2. `python -m pmauto queue` re-checks for client changes later in the day; `python -m pmauto pm` rebuilds forecasts, risks and the executive report on its own.
  3. Meeting step for any transcript not yet processed.
  4. Work the list oldest first: analyse each ticket and save the files, then give the PM one table: key, verdict, one-line reason, number of questions, recommended action.
  Aim to finish within the PM's first office hour; if the list is too long, say how many are left rather than rushing.
- **"analyse KEY"**: context, method, save both files, check the comment with a dry-run post, then a 5-line summary.
- **"post it" (for KEY)**: right before posting, re-read the ticket's status and newest comments; if it has moved past the question stage (QA, Done) or the client already answered, tell the PM first. Then dry run, show size and that it is visible to everyone, then `--yes` only after the PM confirms. A second comment for the same analysis needs `--again` and the PM's word.
- **As soon as the orchestrator accepts an analysis**: `python -m pmauto mark KEY --file <path> --verdict <VERDICT>`. Marking takes the ticket off the work list; posting is tracked separately. If `startpm` prints "WARNING ... not marked", mark it first.
- **"wrap up"**: `python -m pmauto report` writes the end-of-day ticket report (`.md` + `.html`); open it with `pmauto show`, then run `python -m pmauto close-windows`.

## Meeting step (only if `meetings` is configured)

1. Find the meeting series in the PM's calendar (ms365 `get-calendar-view`, subject from `meetings.series_subject`). The series' online-meeting id is base64 of `1*<organizerOid>*0**<threadId>` taken from its join URL.
2. List transcripts with ms365 `download-bytes-to-file` on `/me/onlineMeetings/<id>/transcripts`; fetch new ones from `.../transcripts/<tid>/content` with accept `text/vtt` into `<analyses_dir>/meetings/`.
3. Keep only the configured projects and the team members named in the project notes; other teams' segments are skipped.
4. Tickets are usually described, not numbered. Match a discussion to a ticket only when the description clearly fits one open ticket; otherwise list it as a "possible match" for the PM. Never guess.
5. For each match, draft a client-safe comment in `<analyses_dir>/KEY-meeting-comment.md` (the file name must start with the ticket key): "From the <meeting> on <date>: <who> said <decision/clarification>", with a short quote and timestamp. If it changes the requirement, put the ticket back on the work list.
6. Non-ticket items that matter (outages, client commitments) go into the morning summary as follow-ups, not into Jira.
