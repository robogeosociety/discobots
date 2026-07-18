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
// The daily 08:00 PT check-in (ops/dev_checkin.py) is NOT lifted here — it is
// the planned second cron on this Worker.

const HUMAN_TASK_LABEL = "human-task";
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

async function postEmbeds(env, embeds) {
  const channel = await heartbeatChannelId(env);
  for (let i = 0; i < embeds.length; i += EMBEDS_PER_MESSAGE) {
    await discord(env, "POST", `/channels/${channel}/messages`, {
      embeds: embeds.slice(i, i + EMBEDS_PER_MESSAGE),
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
    await postEmbeds(env, embeds);
  } else {
    console.log("no new relevant events");
  }
  await state.save(); // after the post: a failed post retries next run
  return embeds.length;
}

export default {
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(heartbeat(env));
  },

  // No inbound surface — the Worker is cron-driven. (Local test:
  // `wrangler dev --test-scheduled`, then GET /__scheduled.)
  async fetch() {
    return new Response("github-heartbeat: cron-driven (*/30 * * * *); see workers/github-heartbeat/README.md\n");
  },
};
