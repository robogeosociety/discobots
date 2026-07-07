# docs/CODING.md — the coding workflow (Claude Code only)

Progressive-disclosure detail behind [`../CLAUDE.md`](../CLAUDE.md). Read this when you're
about to write code for discobots. Local LLMs don't write code and don't read this.

## 1. Resolve your home repo first

Every coding agent has exactly one home repo. Find it before editing anything:

```sh
pwd
git -C "$(pwd)" remote get-url origin   # → your home repo, or nothing
```

- **Has an origin** → that's where your code goes. (Channel coders: `discobots`, under your
  subdir — `ops/`, `maps/`, …. See the table in [`../AGENT.md`](../AGENT.md).)
- **No origin** (e.g. a general `/Volumes/dev` session) → you roam many repos; `cd` into the
  **existing** project that owns the change. Don't pick a repo at random.

## 2. Never create a new GitHub repo

No `gh repo create`, no `git init` + new remote, no "I'll just make a repo to commit to."
A new top-level repo is a structural decision that's Tommy's to make.

- If you genuinely think a new repo is warranted, **stop and ask** — describe the change and
  let Tommy decide where it lives.
- The failure this prevents: a maps agent, not knowing MapBot belonged in `discobots`, created
  a stray `robogeosociety/discord-maps` repo and committed there. The code had to be re-homed into
  `discobots/maps/` and the stray repo deleted. One sentence of repo-context would have
  avoided the whole detour.

## 3. Commit & PR inside your home repo

- **Worktree per task.** Branch work happens in a git worktree, never by switching branches on
  a shared checkout (multiple agents/sessions touch these trees):
  `git worktree add /Volumes/dev/<repo>.wt/<branch> origin/main -b <branch>`, work there,
  `git worktree remove` once merged.
- **PR-newspaper framework.** Every PR description is a single "newspaper" panel that fits an
  iPad-mini portrait page. Read `~/.claude/pr-framework/PR_FRAMEWORK.md`, write the body, and
  validate with `~/.claude/pr-framework/validate_pr.py <body.md>` before opening/updating.
  Regenerate the whole body on every push — never append.
- **Commit messages** end with the `Co-Authored-By: Claude …` trailer; PR bodies end with the
  Claude Code generation footer.

## 4. Tooling & auth — the holy trinity

- **`gh` / `wrangler` / `npx`+`uv`** over any MCP equivalent. Authenticate via each CLI's code
  flow (`gh auth login`, `wrangler login`); **never mint or hardcode tokens.**
- **Lint/format is manual** (ruff / biome / rustfmt+clippy) — there's no enforced pre-commit/CI
  gate here. Run it yourself before a PR.
- **Secrets never enter this repo or an image** — `ops/run.sh` injects them at `docker run`
  from the host's `.env` files. Add a webhook URL to `observability/grafana/.env`, not here.

## 5. Where things run

The discobots themselves run as OrbStack containers on the always-on Mac mini, built + managed
from the Air via the repo-root `justfile` (`just deploy` / `up` / `down` / `logs`). See
[`../ops/README.md`](../ops/README.md). tommybot (MLX RAG) stays a `raw_exec` Nomad job on the
host. Your channel session itself is a tmux'd `claude --channels` process kept alive by the
launchd babysitter (see [`../AGENT.md`](../AGENT.md)).
