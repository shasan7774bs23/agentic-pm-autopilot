# pm-autopilot: rules for Claude Code in this folder

- Read `README.md` for what the system does. Tickets are worked with `.claude/skills/ticket-analyst/SKILL.md`; a new machine is set up with `.claude/skills/pm-setup/SKILL.md`. Run `python -m pmauto info` at the start of a session to see this machine's projects and settings; if it says NOT SET UP, use the pm-setup skill.
- **The main Claude Code session is the orchestrator.** It decides which model each task goes to, hands the task to a subagent, and **reviews every result before the PM sees it**. A result that fails review is sent back with the specific gaps, or redone on a stronger model. **Quality of the output comes first; token economy second.**
- **Model choice:** the least token-hungry model that reliably meets the quality bar for that task (`models` in the config), at low effort. Scripts before models; `haiku` before `sonnet`; `opus` only for a judgement that is expensive to get wrong, or when the PM asks. Name the model used, and the review outcome, in every summary.
- Never write to the product repos or anyone's clone of them; read code only from the mirrors.
- Never post to Jira without the PM's explicit "post it" for that ticket. Comments are plain business English only (`KEY-comment.md`).
- Never assign tickets or change status, sprint or fields.
- Never print, copy or commit secrets (`~/.pmauto/credentials.env`, test-account rosters). Never put client data (team configs, reports, analyses, digests) in this public repo.
- **This repo is public and generic.** No project, client, person or company names in code, tests, docs or examples; anything team-specific goes in the team config or project notes, which live in a private place.
- Commits carry the PM's name only: no Co-Authored-By trailer and no "Generated with" footer.
- **Test browsers:** reports open in the PM's own browser (`python -m pmauto show <file>`). Every test/automation browser Claude Code starts must be closed when the task is done: `python -m pmauto close-windows`, and `browser_close` for any Playwright MCP browser. Never close the PM's own browser.
