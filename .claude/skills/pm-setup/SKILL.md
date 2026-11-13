---
name: pm-setup
description: One-time setup of pm-autopilot on a new machine, for all of the person's projects at once. Use when someone says "pm setup", "set up pm-autopilot", "install", "configure my projects", "add a project", or when `python -m pmauto info` / `startpm` says the machine is not set up. Checks prerequisites, reads Jira to pre-fill everything it can, asks only what Jira cannot tell, writes the config, and proves it works.
---

# pm-setup: get a new machine fully working, once

Goal: after this conversation the person can say "startpm" every morning and get the same results the original machine gets, for **their** projects. Ask everything in **one** interview, for all projects together, and never ask what Jira or the machine can tell you.

## Rules

- **Secrets never go through the chat.** The Jira API token, passwords and roster files are typed by the person into files on their machine. Never ask them to paste a token; never print `credentials.env` or a roster file; never write a secret into any config or repo.
- **Nothing outward.** Setup only reads Jira and creates files in `~/.pmauto`, its own folders and the bare mirrors. It never posts, never changes Jira, never touches a developer's working clone.
- **Team config is private.** It names clients, people and systems: it stays on the person's machine (`~/.pmauto/teams/<team>.json` by default), never in this public repo.
- Ask in plain language, grouped, with your pre-filled guess shown so they can just confirm. Prefer one message with all questions of a group over many small ones.

## Steps

1. **Check the machine:** `python -m pmauto setup`. It creates `~/.pmauto/` and a `credentials.env` template on first run and lists what is missing (Python 3.11+, Git, Node 20+, Claude Code). Install what you can with their OK; for anything needing admin rights, give the exact command and wait.
2. **Jira sign-in:** ask them to open `~/.pmauto/credentials.env` (give the full path; offer `notepad <path>` on Windows / `open -e <path>` on macOS) and fill in their Jira site, email and API token (link in the file). Then re-run `python -m pmauto setup` until "Jira sign-in" passes. If a team-wide config already exists (a teammate set it up), ask for its path now and skip to step 5 for the personal questions only.
3. **Which projects:** ask for the Jira project keys they manage (all of them, comma-separated). Then run `python -m pmauto discover --projects KEY1,KEY2 > <scratch>/discover.json` and read it.
4. **Interview (one round per group, pre-filled from discover):**
   - **About you:** name to show on reports; time zone (offset from UTC and a label; Jira's profile `timeZone` is only a hint and is often a default, so always confirm); working days; where reports and analyses should go (default `~/PM-Automation`).
   - **Projects:** a short name for each project key (shown on the dashboard); which board is the delivery board when there are several (scrum boards first).
   - **Fields:** confirm the story-points and sprint fields (discover lists candidates with how many recent tickets fill each; suggest the one with data).
   - **Workflow:** show every status with its suggested group and ask them to confirm or move each: `true_done`, `ready_to_deploy`, `qa`, `not_started`, `parked` (statuses where finished work waits, if any), plus `in_progress` and `review` (code or peer review). Ask which board column counts as "completed" in sprint reports (usually the last column: if it holds only Done, keep `velocity_done_key` = `sp_true_done`).
   - **Clients:** show the top reporters; ask which accounts are the client (people and integration accounts such as a Salesforce or service-desk connector). These drive "new client ticket" and "waiting on us".
   - **Estimates:** from discover's counts, confirm how estimates are written: a comment with labels (discover lists the labels it saw, e.g. `DEV`, `QA`; ask which stage each covers: `dev`, `review` or `qa`), Jira Original Estimate, or story points only; how many hours one estimate unit is; and hours per story point (default 8).
   - **Code (optional but recommended):** per project, the git URL, the main branch analyses should read, and (optional) the file and pattern that carries the version number. Mirrors are bare clones in `mirror_root`; their own clones are never used.
   - **Live app (optional):** per project, the site URL to compare against; the path of their test-account roster JSON (kept outside any repo) and which account to use for the version check; sign-in page selectors only if the defaults fail; a regex for the version text if it is not like `v1.2.3`.
   - **Meetings (optional):** the recurring client meeting's subject, if transcripts should be read (needs the Microsoft 365 connector signed in).
   - **Daily digest by GitHub (optional):** whether they want a scheduled morning digest email. If yes, it must run from a **private** repo of theirs (see `examples/private-layer/`): ask for that repo name and their GitHub login.
   - **Client reference (optional):** if client tickets carry the client's own number in the title (a case or request number), ask for an example title and write a regex whose first group is the number, with a short label.
   - **Clarification sprint (optional):** whether the team parks tickets that need client answers in a named sprint. If the board also holds other dated non-delivery sprints, ask for a `delivery_sprint_pattern` (regex) that real sprint names match.
   - **Release branches (optional):** if a project's code is read from its newest release branch, the branch naming (e.g. `release/1.4.0`) as a regex whose groups are the version numbers, plus a fallback branch.
   - **Words the comment checker should allow:** their product names and acronyms (e.g. app names) so plain-English comments may use them.
   - **Project notes:** offer to create `<team folder>/project-notes.md` with what they tell you about the domain, user roles, accounts that lock out, where tickets needing discussion go, estimate format, and the team members in client meetings. The analyst reads it every session.
5. **Write it:** put all answers in one JSON file in your scratch folder using these keys: `owner_name, team_name, timezone_offset_hours, timezone_label, workdays, analyses_dir, reports_dir, mirror_root, projects, project_names, boards, fields{story_points, sprint}, status_groups{not_started, in_progress, review, qa, parked, ready_to_deploy, true_done}, client_accounts{accountId: name}, hours_per_story_point, estimate_unit_hours, velocity_done_key, code_repos{KEY: {repo: "{mirror_root}/<name>.git", branch (or "newest-release"), remote, version_file?, release_branch_pattern?, fallback_branch?}}, estimate_comment_labels{LABEL: dev|review|qa}, client_reference?{label, pattern}, clarification_sprint?, delivery_sprint_pattern?, live_sites{KEY: {url}}, test_accounts_file, live_account, login?, version_pattern?, meetings?{series_subject}, digest_repo?, notify_login?, allowed_terms, project_notes?, team_config` (status names in lower case). `team_config` is where the team file goes (default `~/.pmauto/teams/<team>.json`, on this machine). Then `python -m pmauto init --answers <file>`.
6. **Prove it:** `python -m pmauto setup` (all non-optional checks PASS; it clones the mirrors and installs the browser), then `python -m pmauto info`, then `python -m pmauto startpm`. Show them the overview page and the executive report. Fix anything that fails and run again. Close test browsers: `python -m pmauto close-windows`.
7. **Hand over:** tell them in five lines what runs every morning ("startpm"), how to analyse a ticket ("analyse KEY"), that nothing is posted without "post it", where reports land, and how to change answers later (say "pm setup" again: it keeps what is there and asks only what they want to change).

## Adding or changing projects later

Run `python -m pmauto discover` for the new keys only, ask only the per-project groups, merge them into the existing answers (read the current config with `python -m pmauto info --json`), and run `init` again; the previous machine config is kept as `.bak`, and an existing team config is only replaced when they say so (`overwrite_team_config: true`).
