// skills-feed — the #skills announcer as a Workers cron (issue #56,
// CICD-everything rgs#167 WS2 — lift #4, the last non-WS5-blocked bot).
//
// The Workers port of ops/skills_discord.py's two modes, one per cron trigger:
//   0 */3 * * *   announce every NEW skill the fleet has gained (🆕), seeding
//                 silently + posting one intro on the very first run
//   33 16 * * *   the daily 💡 spotlight on one existing, not-recently-featured
//                 skill (deterministic counter rotation, cooldown 8)
//
// The container read the mini's ~/.claude/{skills,plugins} directly — a Worker
// can't. The inventory-source rework (the WS2 Phase-1 flag): a tiny mini-side
// publisher (supervisor job `skills-inventory`, robogeosociety/supervisor —
// rides the supervisor's existing ~2-min autodeploy lane) scans those trees and
// PUTs a JSON inventory into this Worker's KV namespace via the Cloudflare API
// on change. This Worker only ever READS `inventory` and owns `state` (known
// skills / spotlight rotation) — the same StateFile shapes as the container.
//
// Inventory doc (written by the publisher):
//   { "generated_at": epoch, "skills": [ {key, name, description, source, since} ] }
// `key` is the container's stable version-independent id ("global:<dir>" /
// "plugin:<mkt>/<plugin>/<skill>") so a plugin version bump never re-announces.

const STATE_KEY = "state";
const INVENTORY_KEY = "inventory";
const SPOTLIGHT_CRON = "33 16 * * *"; // ≈ 09:33 PDT (UTC-only crons — 08:33 PT under winter PST)
const DESC_LIMIT = 600; // keep embeds skimmable; full text lives in the SKILL.md
const SPOTLIGHT_COOLDOWN = 8; // how long to avoid re-spotlighting a skill
const INVENTORY_MAX_AGE_H = 48; // warn when the publisher looks wedged
const EMBEDS_PER_MESSAGE = 10;

// discokit.tokens palette.
const COLOR_NEW = 0x3fb950; // OPERATIONAL
const COLOR_SPOTLIGHT = 0x5865f2; // BLURPLE
const COLOR_INIT = 0x8b949e; // UNKNOWN grey

// ── Discord (bot-token REST, the fleet's Workers pattern) ────────────────────

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

async function skillsChannelId(env, state) {
  if (state.channel_id) return state.channel_id;
  const guilds = await discord(env, "GET", "/users/@me/guilds");
  for (const g of guilds) {
    const chans = await discord(env, "GET", `/guilds/${g.id}/channels`);
    const hit = chans.find((c) => c.type === 0 && c.name === env.SKILLS_CHANNEL);
    if (hit) {
      state.channel_id = hit.id; // persisted with the state doc
      return hit.id;
    }
  }
  throw new Error(`channel #${env.SKILLS_CHANNEL} not found in any guild the bot is in`);
}

async function postEmbeds(env, state, embeds) {
  const channel = await skillsChannelId(env, state);
  for (let i = 0; i < embeds.length; i += EMBEDS_PER_MESSAGE) {
    await discord(env, "POST", `/channels/${channel}/messages`, {
      embeds: embeds.slice(i, i + EMBEDS_PER_MESSAGE),
    });
  }
}

// ── embeds (ops/skills_discord.py, verbatim shapes) ──────────────────────────

const clip = (text, limit = DESC_LIMIT) => {
  text = (text || "").split(/\s+/).join(" ");
  return text.length <= limit ? text : text.slice(0, limit - 1).trimEnd() + "…";
};

const newSkillEmbed = (s) => ({
  title: `🆕 New skill: ${s.name}`,
  description: clip(s.description),
  color: COLOR_NEW,
  footer: { text: `source: ${s.source}  ·  /${s.name}` },
});

function spotlightEmbed(s) {
  const e = {
    title: `💡 Skill spotlight: ${s.name}`,
    description: clip(s.description),
    color: COLOR_SPOTLIGHT,
    footer: { text: `source: ${s.source}  ·  /${s.name}  ·  an oldie worth remembering` },
  };
  if (s.since) e.timestamp = new Date(s.since * 1000).toISOString();
  return e;
}

// ── modes ────────────────────────────────────────────────────────────────────

async function loadInventory(env) {
  const doc = await env.STATE.get(INVENTORY_KEY, "json");
  const skills = doc?.skills;
  if (!Array.isArray(skills) || !skills.length) {
    console.warn("no inventory in KV yet — is the mini publisher (supervisor skills-inventory) wired?");
    return null;
  }
  const age = Date.now() / 1000 - (doc.generated_at || 0);
  if (age > INVENTORY_MAX_AGE_H * 3600) {
    console.warn(`inventory is ${Math.round(age / 3600)}h old — publisher may be wedged (using it anyway)`);
  }
  return new Map(skills.map((s) => [s.key, s]));
}

/** Announce new skills; the very first run seeds silently + posts one intro. */
async function runNew(env) {
  const inv = await loadInventory(env);
  if (!inv) return;
  const state = (await env.STATE.get(STATE_KEY, "json")) || {};
  const known = state.skills || {};
  const firstRun = !state.initialized;

  const fresh = [...inv.values()].filter((s) => !(s.key in known));
  for (const s of inv.values()) {
    if (!(s.key in known)) known[s.key] = { first_seen: s.since, name: s.name };
  }
  state.skills = known;
  state.initialized = true;

  if (firstRun) {
    const names = [...inv.values()].map((s) => s.name).sort().join(", ");
    await postEmbeds(env, state, [{
      title: "📚 Skills tracker online",
      description:
        `Now watching **${inv.size}** skills across the fleet. New ones land here as the ` +
        `bots pick them up, with the occasional 💡 spotlight on an existing favourite.` +
        `\n\n_Currently tracked:_ ${clip(names, 800)}`,
      color: COLOR_INIT,
    }]);
    await env.STATE.put(STATE_KEY, JSON.stringify(state));
    console.log(`first run — seeded ${inv.size} skills, posted intro`);
    return;
  }

  if (!fresh.length) {
    console.log("no new skills");
    return; // nothing changed — no KV write either
  }
  fresh.sort((a, b) => a.name.localeCompare(b.name));
  await postEmbeds(env, state, fresh.map(newSkillEmbed));
  await env.STATE.put(STATE_KEY, JSON.stringify(state));
  console.log(`posted ${fresh.length} new skill(s): ${fresh.map((s) => s.name).join(", ")}`);
}

/** One 💡 spotlight — deterministic counter rotation over a cooldown-filtered pool. */
async function runSpotlight(env) {
  const inv = await loadInventory(env);
  if (!inv) return;
  const state = (await env.STATE.get(STATE_KEY, "json")) || {};

  const recent = state.spotlight_recent || [];
  let pool = [...inv.keys()].filter((k) => !recent.includes(k));
  if (!pool.length) pool = [...inv.keys()];
  pool.sort();

  const idx = state.spotlight_counter || 0;
  const chosenKey = pool[idx % pool.length];
  const chosen = inv.get(chosenKey);

  state.spotlight_counter = idx + 1;
  state.spotlight_recent = [chosenKey, ...recent].slice(0, SPOTLIGHT_COOLDOWN);

  await postEmbeds(env, state, [spotlightEmbed(chosen)]);
  await env.STATE.put(STATE_KEY, JSON.stringify(state));
  console.log(`spotlight: ${chosen.name} (${chosen.source})`);
}

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(controller.cron === SPOTLIGHT_CRON ? runSpotlight(env) : runNew(env));
  },

  // No inbound surface — cron-driven. (Local test: `wrangler dev
  // --test-scheduled`, then GET /__scheduled?cron=...)
  async fetch() {
    return new Response("skills-feed: cron-driven (0 */3 + 33 16 UTC); see workers/skills-feed/README.md\n");
  },
};
