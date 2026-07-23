// github-heartbeat — the #dev development heartbeat as a Workers cron (issue #56,
// CICD-everything rgs#167 WS2 — first lift off the mini).
//
// The Workers port of ops/github_discord.py's core beat: every 30 minutes,
// announce each exactly once to #dev —
//   • PRs opened / merged and issues opened / closed across the org
//   • releases published
//   • red CI on default branches
//   • human-task board changes (issues labeled `human-task` opened / closed /
//     commented)
//
// Differences from the container bot, by construction of the platform:
//   • GitHub auth is the rgs-deploy-gate GitHub App (JWT → org installation
//     token), not tommyroar's `gh` token — so activity comes from per-repo REST
//     scans over recently-pushed repos, not the user org-dashboard events feed
//     (that feed is user-token-only). A scan category the app lacks permission
//     for logs a warning and skips — it heals the run the permission lands.
//   • Posting is bot-token REST to the #dev channel (the deploy-gate pattern),
//     not a webhook — no mini-side .env to reach.
//   • State (the seen-id gate + the human-task board snapshot) lives in KV
//     under one key, mirroring discokit.notify's StateFile/ChangeFeed shapes.
//
// Second cron (daily 15:00 UTC): the dev check-in — the Workers port of
// ops/dev_checkin.py's template-rendered morning status embed (PRs merged
// since the last check-in, CI health chips, the open human-task queue,
// upcoming repo-sync). The two beats share auth, scans, posting, and the one
// KV state doc; `controller.cron` picks the beat.

const HUMAN_TASK_LABEL = "human-task";
const OPERATOR_ID = "1382748563355734127"; // @tommyroar — pinged when a human task waits
const CHECKIN_CRON = "3 15 * * *"; // daily check-in; anything else = the 30-min heartbeat
// (see wrangler.toml for the PT/DST caveat and the :03 anti-collision offset)
const CI_HEALTH_DAYS = 7; // check-in CI chips cover repos pushed this recently
const MAX_LIST = 8; // cap per check-in section so the embed stays one screenful
const EVENT_MAX_AGE_HOURS = 24; // never announce older than this (fresh KV must not replay history)
const ACTIVE_DAYS = 3; // only scan repos pushed this recently (bounds the API calls)
const SEEN_CAP = 500; // seen-id list cap, oldest dropped (discokit.ChangeFeed)
const PER_REPO_ITEMS = 10; // recent PRs / issues fetched per repo per run
const STATE_KEY = "state";
const EMBEDS_PER_MESSAGE = 10; // Discord's hard cap per message

// discokit.tokens palette (ops/discokit/tokens.json — the ONE status palette).
const COLOR_MERGE = 0x8957e5; // PURPLE
const COLOR_PR_OPEN = 0x3fb950; // OPERATIONAL
const COLOR_CI_FAIL = 0xf85149; // CRITICAL
const COLOR_RELEASE = 0xa371f7; // MAINTENANCE
const COLOR_ISSUE = 0x58a6ff; // INFO
const COLOR_ISSUE_CLOSED = 0x8b949e; // UNKNOWN grey
const COLOR_TASK_OPEN = 0xd29922; // DEGRADED — waiting on Tommy
const COLOR_TASK_DONE = 0x3fb950; // OPERATIONAL
const COLOR_TASK_NOTE = 0x58a6ff; // INFO

const enc = new TextEncoder();

// ── GitHub App auth (the deploy-gate pattern) ────────────────────────────────

function b64url(bytes) {
  return btoa(String.fromCharCode(...new Uint8Array(bytes)))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function appJwt(env) {
  const now = Math.floor(Date.now() / 1000);
  const header = b64url(enc.encode(JSON.stringify({ alg: "RS256", typ: "JWT" })));
  const payload = b64url(enc.encode(JSON.stringify({ iat: now - 30, exp: now + 540, iss: env.GH_APP_ID })));
  const pem = env.GH_APP_PRIVATE_KEY_PKCS8.replace(/-----[^-]+-----/g, "").replace(/\s+/g, "");
  const der = Uint8Array.from(atob(pem), (c) => c.charCodeAt(0));
  const key = await crypto.subtle.importKey(
    "pkcs8", der, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["sign"],
  );
  const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, enc.encode(`${header}.${payload}`));
  return `${header}.${payload}.${b64url(sig)}`;
}

async function ghRaw(token, method, path, body) {
  return fetch(`https://api.github.com${path}`, {
    method,
    headers: {
      authorization: `Bearer ${token}`,
      accept: "application/vnd.github+json",
      "user-agent": "rgs-github-heartbeat",
      ...(body ? { "content-type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
}

/** GET a GitHub path; null (with a warning) on any error — a failed scan
 *  category skips this run and self-heals on a later one. */
async function gh(token, path) {
  let res;
  try {
    res = await ghRaw(token, "GET", path);
  } catch (err) {
    console.warn(`gh GET ${path} threw: ${err}`);
    return null;
  }
  if (!res.ok) {
    console.warn(`gh GET ${path} → ${res.status} (missing app permission?)`);
    return null;
  }
  return res.json();
}

async function orgInstallationToken(env) {
  const jwt = await appJwt(env);
  const inst = await ghRaw(jwt, "GET", `/orgs/${env.GH_ORG}/installation`);
  if (!inst.ok) throw new Error(`org installation lookup ${inst.status}`);
  const { id } = await inst.json();
  const tok = await ghRaw(jwt, "POST", `/app/installations/${id}/access_tokens`);
  if (!tok.ok) throw new Error(`installation token ${tok.status}`);
  return (await tok.json()).token;
}

// ── Discord (bot-token REST, the deploy-gate pattern) ────────────────────────

let channelCache = null; // { name, id } — survives warm isolates

async function discord(env, method, path, body) {
  const res = await fetch(`https://discord.com/api/v10${path}`, {
    method,
    headers: {
      authorization: `Bot ${env.DISCORD_BOT_TOKEN}`,
      ...(body ? { "content-type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`discord ${method} ${path} ${res.status}: ${(await res.text()).slice(0, 200)}`);
  return res.status === 204 ? null : res.json();
}

async function heartbeatChannelId(env) {
  if (channelCache?.name === env.HEARTBEAT_CHANNEL) return channelCache.id;
  const guilds = await discord(env, "GET", "/users/@me/guilds");
  for (const g of guilds) {
    const chans = await discord(env, "GET", `/guilds/${g.id}/channels`);
    const hit = chans.find((c) => c.type === 0 && c.name === env.HEARTBEAT_CHANNEL);
    if (hit) {
      channelCache = { name: env.HEARTBEAT_CHANNEL, id: hit.id };
      return hit.id;
    }
  }
  throw new Error(`channel #${env.HEARTBEAT_CHANNEL} not found in any guild the bot is in`);
}

async function postEmbeds(env, embeds, content) {
  const channel = await heartbeatChannelId(env);
  for (let i = 0; i < embeds.length; i += EMBEDS_PER_MESSAGE) {
    await discord(env, "POST", `/channels/${channel}/messages`, {
      embeds: embeds.slice(i, i + EMBEDS_PER_MESSAGE),
      // the mention rides the first message only — one ping per post, not per chunk
      ...(content && i === 0
        ? { content, allowed_mentions: { users: [OPERATOR_ID] } }
        : {}),
    });
  }
}

// ── State (discokit StateFile + ChangeFeed shapes, on KV) ────────────────────

async function loadState(env) {
  let doc = {};
  try {
    doc = (await env.STATE.get(STATE_KEY, "json")) || {};
  } catch {
    doc = {};
  }
  const seen = Array.isArray(doc.seen_ids) ? doc.seen_ids : [];
  return {
    doc,
    seen,
    lookup: new Set(seen),
    /** True exactly once per id; marked seen on first sight. */
    isNew(id) {
      if (!id || this.lookup.has(id)) return false;
      this.seen.push(id);
      this.lookup.add(id);
      return true;
    },
    async save() {
      this.doc.seen_ids = this.seen.slice(-SEEN_CAP);
      await env.STATE.put(STATE_KEY, JSON.stringify(this.doc));
    },
  };
}

// ── Scans ────────────────────────────────────────────────────────────────────

const parseTs = (v) => (v ? Date.parse(v) || null : null);
const authorLink = (login) =>
  login ? `[@${login}](https://github.com/${login})` : "unknown";
const isHumanTask = (labels) =>
  (labels || []).some((l) => l?.name === HUMAN_TASK_LABEL);

async function listActiveRepos(token, now) {
  const cutoff = now - ACTIVE_DAYS * 86_400_000;
  const data = await gh(token, "/installation/repositories?per_page=100");
  const repos = (data?.repositories || []).filter((r) => !r.archived);
  repos.sort((a, b) => (parseTs(b.pushed_at) || 0) - (parseTs(a.pushed_at) || 0));
  return {
    all: repos,
    active: repos.filter((r) => (parseTs(r.pushed_at) || 0) >= cutoff),
  };
}

async function scanPulls(token, repo, state, horizon) {
  const prs = await gh(
    token,
    `/repos/${repo.full_name}/pulls?state=all&sort=updated&direction=desc&per_page=${PER_REPO_ITEMS}`,
  );
  const embeds = [];
  for (const pr of prs || []) {
    const description = `**Repo:** ${repo.full_name}\n**Author:** ${authorLink(pr.user?.login)}`;
    const mergedAt = parseTs(pr.merged_at);
    if (mergedAt && mergedAt >= horizon && state.isNew(`pr-merge:${pr.id}`)) {
      embeds.push({
        title: `PR Merged: #${pr.number} ${pr.title || "Untitled PR"}`,
        description, url: pr.html_url, color: COLOR_MERGE,
      });
    }
    const createdAt = parseTs(pr.created_at);
    if (createdAt && createdAt >= horizon && state.isNew(`pr-open:${pr.id}`)) {
      embeds.push({
        title: `PR Opened: #${pr.number} ${pr.title || "Untitled PR"}`,
        description, url: pr.html_url, color: COLOR_PR_OPEN,
      });
    }
    // closed-without-merge: not newsworthy (the container bot's rule too)
  }
  return embeds;
}

async function scanIssues(token, repo, state, horizon) {
  const issues = await gh(
    token,
    `/repos/${repo.full_name}/issues?state=all&sort=updated&direction=desc&per_page=${PER_REPO_ITEMS}`,
  );
  const embeds = [];
  for (const it of issues || []) {
    if (it.pull_request) continue; // the issues API returns PRs too
    // Human-task issues are the board scan's beat (it tracks comments too) —
    // announcing them here would double-post.
    if (isHumanTask(it.labels)) continue;
    const description = `**Repo:** ${repo.full_name}\n**Author:** ${authorLink(it.user?.login)}`;
    const title = it.title || "Untitled issue";
    const createdAt = parseTs(it.created_at);
    if (createdAt && createdAt >= horizon && state.isNew(`issue-open:${it.id}`)) {
      embeds.push({
        title: `Issue Opened: #${it.number} ${title}`,
        description, url: it.html_url, color: COLOR_ISSUE,
      });
    }
    const closedAt = parseTs(it.closed_at);
    if (closedAt && closedAt >= horizon && state.isNew(`issue-close:${it.id}`)) {
      embeds.push({
        title: `Issue Closed: #${it.number} ${title}`,
        description, url: it.html_url, color: COLOR_ISSUE_CLOSED,
      });
    }
  }
  return embeds;
}

async function scanReleases(token, repo, state, horizon) {
  const releases = await gh(token, `/repos/${repo.full_name}/releases?per_page=3`);
  const embeds = [];
  for (const rel of releases || []) {
    const publishedAt = parseTs(rel.published_at);
    if (!publishedAt || publishedAt < horizon) continue;
    if (!state.isNew(`release:${rel.id}`)) continue;
    const tag = rel.tag_name || "untagged";
    embeds.push({
      title: `🚀 Release: ${repo.full_name} ${rel.name || tag}`,
      description: `**Tag:** ${tag}`,
      url: rel.html_url, color: COLOR_RELEASE,
    });
  }
  return embeds;
}

async function scanCiFailures(token, repo, state, horizon) {
  const branch = repo.default_branch || "main";
  const runs = await gh(
    token,
    `/repos/${repo.full_name}/actions/runs?branch=${branch}&status=completed&per_page=5`,
  );
  const embeds = [];
  for (const run of runs?.workflow_runs || []) {
    if (run.conclusion !== "failure") continue;
    const createdAt = parseTs(run.created_at);
    if (!createdAt || createdAt < horizon) continue;
    if (!state.isNew(`ci:${run.id}`)) continue;
    embeds.push({
      title: `CI Failed: ${run.name || "workflow"}`,
      description: `**Repo:** ${repo.full_name}\n**Branch:** ${branch}`,
      url: run.html_url, color: COLOR_CI_FAIL,
    });
  }
  return embeds;
}

/** Diff the org's human-task board (issues labeled `human-task`) and announce
 *  opened / closed / commented transitions. A fresh state seeds silently so a
 *  brand-new KV namespace never re-announces the whole existing board. */
async function scanHumanTasks(token, repos, state) {
  const doc = state.doc;
  const known = doc.tasks || {};
  const seeded = doc.seeded === true;
  const embeds = [];
  let sawBoard = false;

  for (const repo of repos) {
    if (!repo.has_issues) continue;
    const items = await gh(
      token,
      `/repos/${repo.full_name}/issues?labels=${HUMAN_TASK_LABEL}&state=all&per_page=50`,
    );
    if (items === null) return []; // fetch failed — keep the snapshot, retry next run
    sawBoard = true;
    for (const it of items) {
      if (it.pull_request) continue;
      const key = `${repo.full_name}#${it.number}`;
      const cur = { state: it.state || "open", comments: it.comments || 0 };
      const prev = known[key];
      const ref = `${repo.name}#${it.number}`;
      const title = it.title || "Untitled task";
      if (seeded) {
        if (!prev && cur.state === "open") {
          embeds.push({
            title: `🧭 Human task opened: ${ref} ${title}`,
            description: `**Repo:** ${repo.full_name}`,
            url: it.html_url, color: COLOR_TASK_OPEN,
          });
        } else if (prev?.state === "open" && cur.state === "closed") {
          embeds.push({
            title: `✅ Human task closed: ${ref} ${title}`,
            description: `**Repo:** ${repo.full_name}`,
            url: it.html_url, color: COLOR_TASK_DONE,
          });
        } else if (prev && cur.comments > (prev.comments || 0)) {
          embeds.push({
            title: `💬 Human task activity: ${ref} ${title}`,
            description: `**Repo:** ${repo.full_name}\n**New comments:** ${cur.comments - (prev.comments || 0)}`,
            url: it.html_url, color: COLOR_TASK_NOTE,
          });
        }
      }
      known[key] = cur;
    }
  }

  doc.tasks = known;
  if (sawBoard && !seeded) {
    doc.seeded = true;
    console.log(`seeded human-task board silently (${Object.keys(known).length} task(s))`);
  }
  return embeds;
}

// ── The daily check-in (ops/dev_checkin.py's beat) ───────────────────────────

const issueLine = (repoFull, it) =>
  `• [${repoFull.split("/").pop()}#${it.number}](${it.html_url}) ${it.title || ""}`;

/** PRs merged since `since` across recently-pushed repos, newest first.
 *  (The container used the org-wide search API — user-token territory; a merge
 *  always bumps the repo's pushed_at, so a pushed-since repo walk sees them.) */
async function mergedPrsSince(token, repos, since) {
  const merged = [];
  for (const repo of repos) {
    if ((parseTs(repo.pushed_at) || 0) < since) continue;
    const prs = await gh(
      token,
      `/repos/${repo.full_name}/pulls?state=closed&sort=updated&direction=desc&per_page=30`,
    );
    for (const pr of prs || []) {
      const mergedAt = parseTs(pr.merged_at);
      if (mergedAt && mergedAt >= since) merged.push({ repo: repo.full_name, mergedAt, pr });
    }
  }
  merged.sort((a, b) => b.mergedAt - a.mergedAt);
  return merged;
}

/** (repo name, ✅/🔴/⚪ chip) for each repo pushed within CI_HEALTH_DAYS, from
 *  the latest completed default-branch run. */
async function ciHealth(token, repos, now) {
  const cutoff = now - CI_HEALTH_DAYS * 86_400_000;
  const chips = [];
  for (const repo of repos) {
    if ((parseTs(repo.pushed_at) || 0) < cutoff) continue;
    const branch = repo.default_branch || "main";
    const runs = await gh(
      token,
      `/repos/${repo.full_name}/actions/runs?branch=${branch}&status=completed&per_page=1`,
    );
    const conclusion = runs?.workflow_runs?.[0]?.conclusion;
    chips.push([repo.name, conclusion == null ? "⚪" : conclusion === "success" ? "✅" : "🔴"]);
  }
  return chips;
}

async function openHumanTasks(token, repos) {
  const open = [];
  for (const repo of repos) {
    if (!repo.has_issues) continue;
    const items = await gh(
      token,
      `/repos/${repo.full_name}/issues?labels=${HUMAN_TASK_LABEL}&state=open&per_page=50`,
    );
    for (const it of items || []) {
      if (!it.pull_request) open.push({ repo: repo.full_name, it });
    }
  }
  return open;
}

/** The next scheduled repo-sync: Mondays 07:17 UTC (supervisor repo). */
function nextRepoSync(now) {
  const d = new Date(now);
  d.setUTCHours(7, 17, 0, 0);
  d.setUTCDate(d.getUTCDate() + ((8 - d.getUTCDay()) % 7)); // next Monday (getUTCDay: Mon = 1)
  if (d.getTime() <= now) d.setUTCDate(d.getUTCDate() + 7);
  return d;
}

function capped(lines, total) {
  const out = lines.slice(0, MAX_LIST);
  if (total > MAX_LIST) out.push(`… and ${total - MAX_LIST} more`);
  return out;
}

async function checkin(env) {
  const now = Date.now();
  const token = await orgInstallationToken(env);
  const state = await loadState(env);
  const since = parseTs(state.doc.checkin_last_run) || now - 24 * 3_600_000;
  const { all } = await listActiveRepos(token, now);

  const sections = [];

  const merged = await mergedPrsSince(token, all, since);
  const mlines = capped(merged.map((m) => issueLine(m.repo, m.pr)), merged.length);
  sections.push(
    `**Merged since last check-in (${merged.length})**\n` +
      (mlines.join("\n") || "• a quiet stretch — nothing merged"),
  );

  const chips = await ciHealth(token, all, now);
  const red = chips.filter(([, c]) => c === "🔴").map(([name, c]) => `• ${c} ${name}`);
  const green = chips.filter(([, c]) => c === "✅").length;
  sections.push(
    `**CI on main (${green}/${chips.length} green)**\n` + (red.join("\n") || "• all active lanes green"),
  );

  const tasks = await openHumanTasks(token, all);
  const tlines = capped(tasks.map((t) => issueLine(t.repo, t.it)), tasks.length);
  sections.push(`**Open human tasks (${tasks.length})**\n` + (tlines.join("\n") || "• queue clear 🎉"));

  const sync = nextRepoSync(now);
  sections.push(`**Upcoming**\n• repo-sync <t:${Math.floor(sync.getTime() / 1000)}:F> (supervisor, mini-fleet runner)`);

  const day = new Date(now).toLocaleDateString("en-US", {
    weekday: "short", month: "short", day: "numeric", timeZone: "America/Los_Angeles",
  });
  await postEmbeds(
    env,
    [{
      title: `☕ Dev check-in — ${day}`,
      description: sections.join("\n\n"),
      color: COLOR_ISSUE, // INFO blue, the container's check-in colour
    }],
    tasks.length
      ? `<@${OPERATOR_ID}> ${tasks.length} human task${tasks.length === 1 ? "" : "s"} still waiting`
      : undefined,
  );

  // After the post, like the container: a failed post keeps the window open.
  state.doc.checkin_last_run = new Date(now).toISOString();
  await state.save();
  console.log(`check-in posted (window since ${new Date(since).toISOString()})`);
}

// ── The beat ─────────────────────────────────────────────────────────────────

async function heartbeat(env) {
  const now = Date.now();
  const horizon = now - EVENT_MAX_AGE_HOURS * 3_600_000;
  const token = await orgInstallationToken(env);
  const state = await loadState(env);
  const { all, active } = await listActiveRepos(token, now);

  const embeds = [];
  for (const repo of active) {
    embeds.push(...(await scanPulls(token, repo, state, horizon)));
    embeds.push(...(await scanIssues(token, repo, state, horizon)));
    embeds.push(...(await scanReleases(token, repo, state, horizon)));
    embeds.push(...(await scanCiFailures(token, repo, state, horizon)));
  }
  embeds.push(...(await scanHumanTasks(token, all, state)));

  if (embeds.length) {
    console.log(`posting ${embeds.length} embed(s) to #${env.HEARTBEAT_CHANNEL}`);
    const opened = embeds.filter((e) => e.title?.startsWith("🧭")).length;
    await postEmbeds(
      env,
      embeds,
      opened ? `<@${OPERATOR_ID}> ${opened === 1 ? "a human task needs" : `${opened} human tasks need`} you` : undefined,
    );
  } else {
    console.log("no new relevant events");
  }
  await state.save(); // after the post: a failed post retries next run
  return embeds.length;
}

export default {
  async scheduled(controller, env, ctx) {
    // Two beats, one Worker — the trigger's cron expression picks the beat.
    ctx.waitUntil(controller.cron === CHECKIN_CRON ? checkin(env) : heartbeat(env));
  },

  // No inbound surface — the Worker is cron-driven. (Local test:
  // `wrangler dev --test-scheduled`, then GET /__scheduled.)
  async fetch() {
    return new Response("github-heartbeat: cron-driven (*/30 * * * *); see workers/github-heartbeat/README.md\n");
  },
};
