# Your private layer (optional)

Keep everything that names your clients, people and systems in a **private** repo of your own, next to the public tool:

```
your-private-pm-layer/            (PRIVATE repo)
├── config/team.json              team config written by `pm setup` (projects, statuses, clients, repos, sites)
├── project-notes.md              what the analyst should know about your domain, roles and rules
├── ci-config.json                settings for the scheduled digest (see the workflow header)
└── .github/workflows/daily-digest.yml   copied from this folder
```

- Teammates clone it and give `config/team.json` to `pm setup`, so they only answer the personal questions.
- The scheduled digest runs here, so the issue (with ticket text) and its email stay private. Add the three Jira secrets in the repo settings.
- Set `digest_repo` in your config to `<you>/<this repo>` so `startpm` shows the day's digest items and the overview page shows what GitHub ran.

Never put credentials or test-account rosters in this repo either: they stay in `~/.pmauto/` and your own files.
