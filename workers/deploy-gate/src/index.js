// deploy-gate — the org's deploy-approval gate (robogeosociety/discobots#55).
//
// Two inbound routes:
//   POST /github        GitHub `deployment_protection_rule` webhook (HMAC-verified)
//                       → posts an Approve/Reject card to #dev.
//   POST /interactions  Discord interactions (Ed25519-verified): the card's buttons
//                       and the /deploy approve|reject slash command
//                       → answers GitHub's deployment callback.
//
// GitHub auth is the rgs-deploy-gate GitHub App (JWT → installation token).

const enc = new TextEncoder();

// ── generic helpers ──────────────────────────────────────────────────────────

function hexToBytes(hex) {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
  return out;
}

function b64url(bytes) {
  return btoa(String.fromCharCode(...new Uint8Array(bytes)))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status, headers: { "content-type": "application/json" },
  });
}

// ── GitHub App auth ──────────────────────────────────────────────────────────

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

async function gh(env, token, method, path, body) {
  const res = await fetch(`https://api.github.com${path}`, {
    method,
    headers: {
      authorization: `Bearer ${token}`,
      accept: "application/vnd.github+json",
      "user-agent": "rgs-deploy-gate",
      ...(body ? { "content-type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  return res;
}

async function installationToken(env, repo) {
  const jwt = await appJwt(env);
  const inst = await gh(env, jwt, "GET", `/repos/${env.GH_ORG}/${repo}/installation`);
  if (!inst.ok) throw new Error(`installation lookup ${inst.status}`);
  const { id } = await inst.json();
  const tok = await gh(env, jwt, "POST", `/app/installations/${id}/access_tokens`);
  if (!tok.ok) throw new Error(`installation token ${tok.status}`);
  return (await tok.json()).token;
}

// Answer GitHub's protection-rule callback for one pending run.
async function reviewDeployment(env, repo, runId, envName, state, whoTag) {
  const token = await installationToken(env, repo);
  const res = await gh(env, token, "POST",
    `/repos/${env.GH_ORG}/${repo}/actions/runs/${runId}/deployment_protection_rule`, {
      environment_name: envName,
      state, // "approved" | "rejected"
      comment: `${state} via deploy-gate by Discord user ${whoTag}`,
    });
  if (!res.ok) throw new Error(`review ${res.status}: ${(await res.text()).slice(0, 200)}`);
}

// ── Discord ──────────────────────────────────────────────────────────────────

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

async function deployChannelId(env) {
  if (channelCache?.name === env.DEPLOY_CHANNEL) return channelCache.id;
  const guilds = await discord(env, "GET", "/users/@me/guilds");
  for (const g of guilds) {
    const chans = await discord(env, "GET", `/guilds/${g.id}/channels`);
    const hit = chans.find((c) => c.type === 0 && c.name === env.DEPLOY_CHANNEL);
    if (hit) {
      channelCache = { name: env.DEPLOY_CHANNEL, id: hit.id };
      return hit.id;
    }
  }
  throw new Error(`channel #${env.DEPLOY_CHANNEL} not found in any guild the bot is in`);
}

async function postPendingCard(env, p) {
  const runId = p.deployment_callback_url.match(/\/runs\/(\d+)\//)[1];
  const repo = p.repository.name;
  const runUrl = `${p.repository.html_url}/actions/runs/${runId}`;
  const ref = p.deployment?.ref || "?";
  const creator = p.deployment?.creator?.login || "?";
  const channel = await deployChannelId(env);
  await discord(env, "POST", `/channels/${channel}/messages`, {
    embeds: [{
      title: `🚦 deploy pending — ${repo} → ${p.environment}`,
      description: `[workflow run ${runId}](${runUrl})\nref \`${ref}\` · by \`${creator}\``,
      color: 0xe8a33d,
    }],
    components: [{
      type: 1,
      components: [
        { type: 2, style: 3, label: "Approve", custom_id: `dg|approved|${repo}|${runId}|${p.environment}` },
        { type: 2, style: 4, label: "Reject", custom_id: `dg|rejected|${repo}|${runId}|${p.environment}` },
      ],
    }],
  });
}

// ── request verification ─────────────────────────────────────────────────────

async function verifyDiscord(request, env, bodyText) {
  const sig = request.headers.get("x-signature-ed25519");
  const ts = request.headers.get("x-signature-timestamp");
  if (!sig || !ts) return false;
  const key = await crypto.subtle.importKey(
    "raw", hexToBytes(env.DISCORD_PUBLIC_KEY), { name: "Ed25519" }, false, ["verify"],
  );
  return crypto.subtle.verify("Ed25519", key, hexToBytes(sig), enc.encode(ts + bodyText));
}

async function hmacHex(secret, text) {
  const key = await crypto.subtle.importKey(
    "raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode(text)));
  return [...mac].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function verifyHmacHeader(request, header, secret, bodyText) {
  const sig = request.headers.get(header);
  if (!sig?.startsWith("sha256=")) return false;
  const expected = await hmacHex(secret, bodyText);
  const given = sig.slice(7);
  if (expected.length !== given.length) return false;
  let diff = 0;
  for (let i = 0; i < expected.length; i++) diff |= expected.charCodeAt(i) ^ given.charCodeAt(i);
  return diff === 0;
}

// ── dispatch lane (private repos — no env protection rules without Enterprise) ──

// nonce binds an approval to exactly one (repo, run, tag, sha) tuple
function nonceText(repo, runId, tag, sha) {
  return `${repo}|${runId}|${tag}|${sha}`;
}

async function postDispatchCard(env, req) {
  const { repo, run_id: runId, sha, tag = "-" } = req;
  const runUrl = `https://github.com/${env.GH_ORG}/${repo}/actions/runs/${runId}`;
  const channel = await deployChannelId(env);
  await discord(env, "POST", `/channels/${channel}/messages`, {
    embeds: [{
      title: `🚦 deploy pending — ${repo} → mini`,
      description: `[workflow run ${runId}](${runUrl})\ntag \`${tag}\` · sha \`${(sha || "").slice(0, 12)}\``,
      color: 0xe8a33d,
    }],
    components: [{
      type: 1,
      components: [
        { type: 2, style: 3, label: "Approve", custom_id: `dgd|a|${repo}|${runId}|${tag}|${sha}` },
        { type: 2, style: 4, label: "Reject", custom_id: `dgd|r|${repo}|${runId}|${tag}|${sha}` },
      ],
    }],
  });
}

async function fireDeployDispatch(env, repo, runId, tag, sha) {
  const token = await installationToken(env, repo);
  const nonce = await hmacHex(env.DG_REQUEST_SECRET, nonceText(repo, runId, tag, sha));
  const res = await gh(env, token, "POST", `/repos/${env.GH_ORG}/${repo}/dispatches`, {
    event_type: "deploy-approved",
    client_payload: { tag, sha, run_id: runId, nonce },
  });
  if (res.status !== 204) throw new Error(`dispatch ${res.status}: ${(await res.text()).slice(0, 150)}`);
}

// ── interaction handling ─────────────────────────────────────────────────────

function actorId(interaction) {
  return interaction.member?.user?.id || interaction.user?.id;
}

function isApprover(env, interaction) {
  return env.APPROVER_IDS.split(",").map((s) => s.trim()).includes(actorId(interaction));
}

async function handleInteraction(env, interaction) {
  if (interaction.type === 1) return json({ type: 1 }); // PING → PONG

  if (!isApprover(env, interaction)) {
    return json({ type: 4, data: { content: "⛔ you are not on the approver list", flags: 64 } });
  }
  const who = interaction.member?.user?.username || interaction.user?.username || actorId(interaction);

  // Button click on a dispatch-lane card (private repos)
  if (interaction.type === 3 && interaction.data.custom_id?.startsWith("dgd|")) {
    const [, act, repo, runId, tag, sha] = interaction.data.custom_id.split("|");
    if (act === "a") {
      try {
        await fireDeployDispatch(env, repo, runId, tag, sha);
      } catch (e) {
        return json({ type: 4, data: { content: `❌ ${e.message}`, flags: 64 } });
      }
    }
    const verdict = act === "a" ? "✅ approved" : "🛑 rejected";
    const orig = interaction.message.embeds?.[0] || {};
    return json({
      type: 7,
      data: {
        embeds: [{ ...orig, color: act === "a" ? 0x3d9970 : 0xcc4444,
          title: (orig.title || "").replace("🚦 deploy pending", verdict) }],
        components: [],
        content: `${verdict} by **${who}**${act === "a" ? " — deploy-exec dispatched" : ""}`,
      },
    });
  }

  // Button click on a pending card
  if (interaction.type === 3 && interaction.data.custom_id?.startsWith("dg|")) {
    const [, state, repo, runId, envName] = interaction.data.custom_id.split("|");
    try {
      await reviewDeployment(env, repo, runId, envName, state, who);
    } catch (e) {
      return json({ type: 4, data: { content: `❌ ${e.message}`, flags: 64 } });
    }
    const verdict = state === "approved" ? "✅ approved" : "🛑 rejected";
    const orig = interaction.message.embeds?.[0] || {};
    return json({
      type: 7, // update the card in place: drop buttons, stamp the verdict
      data: {
        embeds: [{ ...orig, color: state === "approved" ? 0x3d9970 : 0xcc4444,
          title: (orig.title || "").replace("🚦 deploy pending", verdict) }],
        components: [],
        content: `${verdict} by **${who}**`,
      },
    });
  }

  // /deploy approve|reject repo:<name> run_id:<id> [env:<name>]
  if (interaction.type === 2 && interaction.data.name === "deploy") {
    const sub = interaction.data.options?.[0];
    const opt = (n) => sub?.options?.find((o) => o.name === n)?.value;
    const state = sub?.name === "approve" ? "approved" : sub?.name === "reject" ? "rejected" : null;
    if (!state) return json({ type: 4, data: { content: "unknown subcommand", flags: 64 } });
    try {
      await reviewDeployment(env, opt("repo"), String(opt("run_id")), opt("env") || "production", state, who);
    } catch (e) {
      return json({ type: 4, data: { content: `❌ ${e.message}`, flags: 64 } });
    }
    return json({ type: 4, data: { content: `${state === "approved" ? "✅" : "🛑"} ${opt("repo")} run ${opt("run_id")} ${state}` } });
  }

  return json({ type: 4, data: { content: "unhandled interaction", flags: 64 } });
}

// ── entry ────────────────────────────────────────────────────────────────────

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") return json({ ok: true });
    if (request.method !== "POST") return new Response("deploy-gate", { status: 200 });
    const bodyText = await request.text();

    if (url.pathname === "/interactions") {
      if (!(await verifyDiscord(request, env, bodyText))) return new Response("bad signature", { status: 401 });
      return handleInteraction(env, JSON.parse(bodyText));
    }

    if (url.pathname === "/request") {
      if (!(await verifyHmacHeader(request, "x-request-signature", env.DG_REQUEST_SECRET, bodyText))) return new Response("bad signature", { status: 401 });
      const req = JSON.parse(bodyText);
      if (!req.repo || !req.run_id || !req.sha) return new Response("missing fields", { status: 400 });
      ctx.waitUntil(postDispatchCard(env, req));
      return json({ ok: true });
    }

    if (url.pathname === "/github") {
      if (!(await verifyHmacHeader(request, "x-hub-signature-256", env.GH_WEBHOOK_SECRET, bodyText))) return new Response("bad signature", { status: 401 });
      const event = request.headers.get("x-github-event");
      if (event === "deployment_protection_rule") {
        const p = JSON.parse(bodyText);
        if (p.action === "requested") ctx.waitUntil(postPendingCard(env, p));
      }
      return json({ ok: true });
    }

    return new Response("not found", { status: 404 });
  },
};
