# CLAUDE.md — discobots (Claude Code only)

You are a **Claude Code coding agent** working in **discobots**, the home repo for the Discord
agent fleet's code (the channel-session bots under `ops/`, `maps/`, …). This file loads only
for Claude Code — local LLMs (qwen, tommybot) never see it; they don't write code. See
[`AGENT.md`](./AGENT.md) for the fleet map and your home-repo row.

## Cardinal rules

1. **You have ONE home repo.** Code you write goes there (for the channel coders that's
   `discobots`, under your subdir). Confirm it before you start: `git -C "$(pwd)" remote get-url origin`.
2. **NEVER create a new GitHub repo.** No `gh repo create`, no fresh `git init` + remote. If
   you think you need one, **stop and ask Tommy.** _(A maps agent that didn't know its code
   belonged here spun up a stray `tommyroar/discord-maps` repo — that's the mistake this rule
   prevents.)_
3. **Commit & PR inside your home repo**, via the **PR-newspaper framework** and a **git
   worktree** — never branch-switch a shared checkout.

## Before a non-trivial change

Read [`docs/CODING.md`](./docs/CODING.md) — the full workflow (home-repo resolution, the
no-new-repo rationale, PR-newspaper + validator, worktree-per-task, holy-trinity auth). It's
progressive disclosure: pull it in when you're about to write code, not before.
