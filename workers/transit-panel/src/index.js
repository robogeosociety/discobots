// transit-panel — the #transit live status panel as a Workers cron (issue #56,
// CICD-everything rgs#167 WS2 — lift #3).
//
// The Workers port of ops/transit_dashboard.py: every minute, fetch the
// OneBusAway GTFS-Realtime alerts feeds (KCM + Sound Transit), reduce them to
// the watched-line chip row (🟢 ok / 🟡🟠🔴 by worst active effect, down-first)
// with the actionable alert headers below, and reconcile ONE Discord message
// that edits itself in place — the discokit.Dashboard contract (content-hash
// diff, freshness stamp bumped only on real change, 404 ⇒ re-post).
//
// Differences from the container daemon, by construction of the platform:
//   • GTFS-RT protobuf is decoded by a ~60-line hand-rolled reader for exactly
//     the FeedMessage→Alert subset the panel needs (no bundler, no deps —
//     matching the repo's single-file Worker convention).
//   • Posting is bot-token REST to the #transit channel (the fleet's Workers
//     pattern), not the mini-side DISCORD_WEBHOOK_TRANSIT webhook.
//   • Panel state (message id + content signature + changed-at + last-good
//     alerts + resolved channel id) lives in one KV key. KV is written ONLY
//     when something changed — an all-quiet minute makes one KV read, two OBA
//     fetches, and zero Discord/KV writes (also keeps well under KV free-tier
//     write limits).
//
// The discrete alert notifier (ops/transit_discord.py FIRING/Cleared embeds)
// is NOT lifted: the panel exists to collapse that churn (~62 msgs/day), and
// its planned cutover retires the notifier rather than porting it.

const STATE_KEY = "state";

// Watched routes, keyed by "<agencyId>_<gtfsRouteId>" (ops/transit_discord.py).
const WATCHED_ROUTES = {
  "1_100252": "Route 7",
  "1_100228": "Route 8",
  "1_100113": "Route 14",
  "1_102574": "Route 554",
  "40_100479": "1 Line",
  "40_2LINE": "2 Line",
};
const LINES = [...new Set(Object.values(WATCHED_ROUTES))];
const AGENCIES = ["1", "40"]; // 1 = King County Metro, 40 = Sound Transit
const OBA_GTFS_RT_BASE = "https://api.pugetsound.onebusaway.org/api/gtfs_realtime";

// discokit.tokens palette (the ONE status palette).
const OPERATIONAL = { dot: "🟢", glyph: "✅", color: 0x3fb950 };
const DEGRADED = { dot: "🟡", glyph: "⚠️", color: 0xd29922 };
const CRITICAL = { dot: "🔴", glyph: "🔴", color: 0xf85149 };
const ORANGE_COLOR = 0xdb6d28; // tokens.ORANGE accent

// Severity ladder: [key, rank, chip dot, embed colour] — worst leads the panel.
const OK = ["ok", 0, OPERATIONAL.dot, OPERATIONAL.color];
const YELLOW_TIER = ["minor", 1, DEGRADED.dot, DEGRADED.color];
const ORANGE_TIER = ["reduced", 2, "🟠", ORANGE_COLOR];
const RED_TIER = ["major", 3, CRITICAL.dot, CRITICAL.color];

const EFFECT_RED = new Set(["NO_SERVICE", "SIGNIFICANT_DELAYS", "DETOUR"]);
const EFFECT_ORANGE = new Set(["REDUCED_SERVICE", "MODIFIED_SERVICE", "STOP_MOVED"]);

// GTFS-RT Alert.Effect enum (gtfs-realtime.proto) — number → name.
const EFFECT_NAMES = {
  1: "NO_SERVICE", 2: "REDUCED_SERVICE", 3: "SIGNIFICANT_DELAYS", 4: "DETOUR",
  5: "ADDITIONAL_SERVICE", 6: "MODIFIED_SERVICE", 7: "OTHER_EFFECT",
  8: "UNKNOWN_EFFECT", 9: "STOP_MOVED", 10: "NO_EFFECT", 11: "ACCESSIBILITY_ISSUE",
};

// ── minimal protobuf reader (just what FeedMessage→Alert needs) ──────────────

/** Iterate (fieldNumber, wireType, value) over one protobuf message buffer.
 *  wire 0 → BigInt varint, wire 2 → Uint8Array slice, wire 1/5 skipped. */
function* fields(buf) {
  let i = 0;
  const varint = () => {
    let shift = 0n, out = 0n;
    for (;;) {
      const b = buf[i++];
      out |= BigInt(b & 0x7f) << shift;
      if ((b & 0x80) === 0) return out;
      shift += 7n;
    }
  };
  while (i < buf.length) {
    const tag = Number(varint());
    const field = tag >> 3, wire = tag & 7;
    if (wire === 0) yield [field, wire, varint()];
    else if (wire === 2) {
      const len = Number(varint());
      yield [field, wire, buf.subarray(i, i + len)];
      i += len;
    } else if (wire === 5) i += 4;
    else if (wire === 1) i += 8;
    else throw new Error(`unsupported wire type ${wire}`);
  }
}

const utf8 = new TextDecoder();

/** TranslatedString → first translation's text (ops _translated). */
function translated(buf) {
  for (const [f, w, v] of fields(buf)) {
    if (f === 1 && w === 2) {
      for (const [tf, tw, tv] of fields(v)) {
        if (tf === 1 && tw === 2) return utf8.decode(tv);
      }
      return "";
    }
  }
  return "";
}

/** EntitySelector → "<agency>_<route>" watched key, or null. */
function selectorKey(buf) {
  let agency = "", route = "";
  for (const [f, w, v] of fields(buf)) {
    if (w !== 2) continue;
    if (f === 1) agency = utf8.decode(v);
    else if (f === 2) route = utf8.decode(v);
  }
  if (!route) return null;
  return agency ? `${agency}_${route}` : route;
}

/** Alert message → {header, effect, routes[]} (watched routes only). */
function decodeAlert(buf) {
  const routes = new Set();
  let effect = "", header = "";
  for (const [f, w, v] of fields(buf)) {
    if (f === 5 && w === 2) {
      const key = selectorKey(v);
      if (key && WATCHED_ROUTES[key]) routes.add(WATCHED_ROUTES[key]);
    } else if (f === 7 && w === 0) {
      const name = EFFECT_NAMES[Number(v)] || "";
      effect = name === "UNKNOWN_EFFECT" ? "" : name;
    } else if (f === 10 && w === 2) header = translated(v);
  }
  return { header, effect, routes: [...routes].sort() };
}

/** FeedMessage bytes → normalised watched-route alerts. */
function decodeFeed(bytes) {
  const out = [];
  for (const [f, w, v] of fields(bytes)) {
    if (f !== 2 || w !== 2) continue; // FeedEntity
    for (const [ef, ew, ev] of fields(v)) {
      if (ef !== 5 || ew !== 2) continue; // FeedEntity.alert
      const alert = decodeAlert(ev);
      if (alert.routes.length) out.push(alert);
    }
  }
  return out;
}

// ── OBA fetch ────────────────────────────────────────────────────────────────

/** All watched-route alerts across agencies, or null if any feed is down
 *  (the container treats a fetch error as "unreachable", keeps last-good). */
async function fetchAlerts(env) {
  const out = [];
  for (const agency of AGENCIES) {
    try {
      const res = await fetch(
        `${OBA_GTFS_RT_BASE}/alerts-for-agency/${agency}.pb?key=${encodeURIComponent(env.OBA_API_KEY)}`,
        { headers: { "user-agent": "rgs-transit-panel" } },
      );
      if (!res.ok) throw new Error(`status ${res.status}`);
      out.push(...decodeFeed(new Uint8Array(await res.arrayBuffer())));
    } catch (err) {
      console.warn(`OBA alerts fetch failed for agency ${agency}: ${err}`);
      return null;
    }
  }
  return out;
}

// ── panel rendering (ops/transit_dashboard.py build_panel, verbatim rules) ───

const truncate = (text, n = 120) => {
  text = (text || "").trim();
  return text.length <= n ? text : text.slice(0, n - 1) + "…";
};

/** Effect → tier, with the header-keyword fallback (_color_for_alert). */
function classify(effect, header) {
  if (EFFECT_RED.has(effect)) return RED_TIER;
  if (EFFECT_ORANGE.has(effect)) return ORANGE_TIER;
  const h = (header || "").toLowerCase();
  if (["detour", "no service", "significant delay"].some((k) => h.includes(k))) return RED_TIER;
  if (["delay", "closed", "closure", "reroute", "suspend", "relocat"].some((k) => h.includes(k))) return ORANGE_TIER;
  return YELLOW_TIER;
}

/** Emoji-dot chip rows, 4 per line (discokit.graph.chips). */
const chips = (items, perLine = 4) => {
  const parts = items.map(([name, dot]) => `${dot} ${name}`);
  const rows = [];
  for (let i = 0; i < parts.length; i += perLine) rows.push(parts.slice(i, i + perLine).join("  "));
  return rows.join("\n");
};

/** Build the panel embed payload. `alerts` null ⇒ feed unreachable. */
function buildPanel(alerts, lastGood) {
  let body = null;
  if (alerts === null) {
    body = `${DEGRADED.glyph} **transit feed unreachable** — showing last known state`;
    alerts = lastGood;
    if (!alerts.length) {
      return { embeds: [{ title: "🚈 transit", description: body, color: DEGRADED.color }] };
    }
  }

  const worst = Object.fromEntries(LINES.map((n) => [n, OK]));
  const header = {};
  for (const a of alerts) {
    const tier = classify(a.effect || "", a.header || "");
    for (const name of a.routes || []) {
      if (name in worst && tier[1] > worst[name][1]) {
        worst[name] = tier;
        header[name] = a.header || "";
      }
    }
  }

  const ordered = [...LINES].sort((a, b) => worst[b][1] - worst[a][1]); // down first
  const chipRow = chips(ordered.map((n) => [n, worst[n][2]]));
  const disrupted = ordered.filter((n) => worst[n][1] > 0);
  const overall = LINES.map((n) => worst[n]).reduce((a, b) => (b[1] > a[1] ? b : a));
  const up = LINES.length - disrupted.length;

  if (body === null) {
    const glyph = disrupted.length ? CRITICAL.glyph : OPERATIONAL.glyph;
    body = `${glyph} **${up}/${LINES.length} lines clear**`;
  }
  const lines = [body, chipRow];
  for (const n of disrupted) {
    const h = truncate(header[n] || "");
    lines.push(h ? `${worst[n][2]} **${n}** — ${h}` : `${worst[n][2]} **${n}**`);
  }

  return {
    embeds: [{
      title: "🚈 transit — watched lines",
      description: lines.join("\n"),
      color: overall[3],
    }],
  };
}

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
  return res;
}

async function resolveChannelId(env) {
  const guilds = await (await discord(env, "GET", "/users/@me/guilds")).json();
  for (const g of guilds) {
    const chans = await (await discord(env, "GET", `/guilds/${g.id}/channels`)).json();
    const hit = chans.find((c) => c.type === 0 && c.name === env.TRANSIT_CHANNEL);
    if (hit) return hit.id;
  }
  throw new Error(`channel #${env.TRANSIT_CHANNEL} not found in any guild the bot is in`);
}

// ── the Dashboard reconcile (discokit.dashboard.Dashboard, on KV) ────────────

/** Deterministic JSON (sorted keys) so the signature is stable, like Python's
 *  json.dumps(sort_keys=True). */
function stableJson(v) {
  if (Array.isArray(v)) return `[${v.map(stableJson).join(",")}]`;
  if (v && typeof v === "object") {
    return `{${Object.keys(v).sort().map((k) => `${JSON.stringify(k)}:${stableJson(v[k])}`).join(",")}}`;
  }
  return JSON.stringify(v);
}

async function sha256hex(text) {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** The freshness stamp — appended at send time, never part of the signature,
 *  so it only bumps when the content really changed. */
function stamped(payload, epoch) {
  const out = structuredClone(payload);
  const e = out.embeds?.[0];
  if (e) e.description = `${e.description || ""}\n\n_updated <t:${epoch}:R> · transit-panel_`.trim();
  return out;
}

async function tick(env) {
  const state = (await env.STATE.get(STATE_KEY, "json")) || {};
  const alerts = await fetchAlerts(env);
  const lastGood = state.last_good || [];

  const payload = buildPanel(alerts, lastGood);
  const sig = await sha256hex(stableJson(payload));
  const now = Math.floor(Date.now() / 1000);

  const changed = sig !== state.sig;
  let dirty = changed;
  if (changed) {
    state.sig = sig;
    state.changed_at = now;
  }
  if (alerts !== null && stableJson(alerts) !== stableJson(lastGood)) {
    state.last_good = alerts;
    dirty = true;
  }

  if (state.message_id && !changed) {
    if (dirty) await env.STATE.put(STATE_KEY, JSON.stringify(state));
    console.log("unchanged");
    return;
  }

  if (!state.channel_id) {
    state.channel_id = await resolveChannelId(env);
    dirty = true;
  }
  const body = stamped(payload, state.changed_at ?? now);

  let outcome;
  if (!state.message_id) {
    outcome = "created";
  } else {
    const res = await discord(env, "PATCH", `/channels/${state.channel_id}/messages/${state.message_id}`, body);
    if (res.status === 404) {
      outcome = "created"; // a human deleted the panel — re-post, re-persist
    } else if (!res.ok) {
      throw new Error(`edit ${res.status}: ${(await res.text()).slice(0, 200)}`);
    } else {
      outcome = "edited";
    }
  }
  if (outcome === "created") {
    const res = await discord(env, "POST", `/channels/${state.channel_id}/messages`, body);
    if (!res.ok) throw new Error(`create ${res.status}: ${(await res.text()).slice(0, 200)}`);
    state.message_id = (await res.json()).id;
    dirty = true;
  }

  if (dirty) await env.STATE.put(STATE_KEY, JSON.stringify(state));
  console.log(outcome);
}

export default {
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(tick(env));
  },

  // No inbound surface — cron-driven. (Local test: `wrangler dev
  // --test-scheduled`, then GET /__scheduled.)
  async fetch() {
    return new Response("transit-panel: cron-driven (* * * * *); see workers/transit-panel/README.md\n");
  },
};
