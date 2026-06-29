/**
 * Discord webhook notifications for campsite availability changes.
 *
 * Part of robot-geographical-society (RGS) — a Cloudflare Workers project
 * that tracks campsite availability on recreation.gov.
 *
 * Usage:
 *   import { notifyCampsiteAvailability, notifyCampsiteUnavailable } from "./discord-campsite-notify";
 *   await notifyCampsiteAvailability(env, changes);
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface AvailabilityChange {
  /** Display name of the individual campsite or loop (e.g. "Loop A - Site 12"). */
  campsiteName: string;
  /** Name of the park or recreation area. */
  parkName: string;
  /** recreation.gov facility ID, used to build direct links. */
  facilityId: string;
  /** ISO-8601 date strings (YYYY-MM-DD) when sites became available. */
  dates: string[];
  /** Current number of available sites. */
  sitesAvailable: number;
  /** Number of sites that were available in the previous check. */
  previouslyAvailable: number;
  /** Optional site classification: "STANDARD", "WALK TO", "GROUP", etc. */
  siteType?: string;
}

export interface Env {
  DISCORD_WEBHOOK_URL: string;
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/** Discord embed field — https://discord.com/developers/docs/resources/message#embed-object-embed-field-structure */
interface EmbedField {
  name: string;
  value: string;
  inline?: boolean;
}

/** Minimal Discord embed shape. */
interface DiscordEmbed {
  title: string;
  color: number;
  fields: EmbedField[];
  footer?: { text: string };
  timestamp?: string;
}

/** Discord webhook payload (embeds variant). */
interface WebhookPayload {
  embeds: DiscordEmbed[];
}

const MAX_FIELDS_PER_EMBED = 10;
const RECREATION_GOV_BASE = "https://www.recreation.gov/camping/campgrounds";

/**
 * Format a compact date range string from an array of ISO dates.
 *
 * Examples:
 *   ["2026-07-04"]                         => "Jul 4"
 *   ["2026-07-04", "2026-07-05"]           => "Jul 4 – 5"
 *   ["2026-07-04", "2026-07-06"]           => "Jul 4, Jul 6"
 *   ["2026-07-04", "2026-07-05", "2026-08-01"] => "Jul 4 – 5, Aug 1"
 */
function formatDateRange(isoDates: string[]): string {
  if (isoDates.length === 0) return "unknown dates";

  const sorted = [...isoDates].sort();

  // Group consecutive dates into runs.
  const runs: Date[][] = [];
  let currentRun: Date[] = [];

  for (const iso of sorted) {
    const d = new Date(iso + "T00:00:00Z");
    if (currentRun.length === 0) {
      currentRun.push(d);
    } else {
      const prev = currentRun[currentRun.length - 1];
      const diffMs = d.getTime() - prev.getTime();
      if (diffMs === 86_400_000) {
        currentRun.push(d);
      } else {
        runs.push(currentRun);
        currentRun = [d];
      }
    }
  }
  runs.push(currentRun);

  const fmt = (d: Date): string => {
    const month = d.toLocaleString("en-US", { month: "short", timeZone: "UTC" });
    const day = d.getUTCDate();
    return `${month} ${day}`;
  };

  return runs
    .map((run) => {
      if (run.length === 1) {
        return fmt(run[0]);
      }
      const first = run[0];
      const last = run[run.length - 1];
      // Same month — compact form.
      if (first.getUTCMonth() === last.getUTCMonth()) {
        return `${fmt(first)}–${last.getUTCDate()}`;
      }
      return `${fmt(first)}–${fmt(last)}`;
    })
    .join(", ");
}

/**
 * Build an embed field for a single availability change.
 */
function buildField(change: AvailabilityChange): EmbedField {
  const link = `${RECREATION_GOV_BASE}/${change.facilityId}`;
  const siteLabel = change.sitesAvailable === 1 ? "site" : "sites";
  const typeTag = change.siteType ? ` (${change.siteType})` : "";
  const dateStr = formatDateRange(change.dates);

  const value = [
    `**${change.campsiteName}**${typeTag}`,
    `${change.sitesAvailable} ${siteLabel} available — ${dateStr}`,
    `[View on recreation.gov](${link})`,
  ].join("\n");

  return {
    name: change.parkName,
    value,
    inline: false,
  };
}

/**
 * Split an array into chunks of at most `size` elements.
 */
function chunk<T>(arr: T[], size: number): T[][] {
  const chunks: T[][] = [];
  for (let i = 0; i < arr.length; i += size) {
    chunks.push(arr.slice(i, i + size));
  }
  return chunks;
}

/**
 * POST a webhook payload to Discord, swallowing errors.
 */
async function postWebhook(url: string, payload: WebhookPayload): Promise<boolean> {
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      console.error(
        `Discord webhook returned ${res.status}: ${await res.text().catch(() => "(unreadable)")}`
      );
      return false;
    }
    return true;
  } catch (err) {
    console.error("Discord webhook request failed:", err);
    return false;
  }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Send a Discord notification when campsites become newly available.
 *
 * Only changes where `previouslyAvailable === 0 && sitesAvailable > 0` are
 * included (i.e. sites that were fully booked and just opened up). If no
 * qualifying changes exist the function returns immediately.
 *
 * Multiple changes are batched into embed fields (up to 10 per embed).
 * If there are more than 10 changes, multiple embeds are sent.
 *
 * This function never throws — Discord failures are logged and swallowed.
 *
 * @returns `true` if at least one webhook call succeeded, `false` otherwise.
 */
export async function notifyCampsiteAvailability(
  env: Env,
  changes: AvailabilityChange[]
): Promise<boolean> {
  // Keep only newly-available sites (were 0, now > 0).
  const newlyAvailable = changes.filter(
    (c) => c.previouslyAvailable === 0 && c.sitesAvailable > 0
  );

  if (newlyAvailable.length === 0) {
    return true; // Nothing to report — not a failure.
  }

  const fields = newlyAvailable.map(buildField);
  const fieldChunks = chunk(fields, MAX_FIELDS_PER_EMBED);

  let anySuccess = false;

  // Discord allows up to 10 embeds per message. We send one message per
  // embed to stay well within rate limits and keep messages readable.
  for (const fieldGroup of fieldChunks) {
    const embed: DiscordEmbed = {
      title: "\u{1F3D5}️ Campsites Just Opened Up",
      color: 0x2ecc71, // green
      fields: fieldGroup,
      footer: {
        text: "robot-geographical-society • Availability tracker",
      },
      timestamp: new Date().toISOString(),
    };

    const ok = await postWebhook(env.DISCORD_WEBHOOK_URL, { embeds: [embed] });
    if (ok) anySuccess = true;
  }

  return anySuccess;
}

/**
 * Send a less-prominent Discord notification when campsites fill up.
 *
 * Only changes where `previouslyAvailable > 0 && sitesAvailable === 0` are
 * included. Uses a red embed with a subdued layout so it doesn't compete
 * with the "just opened" notifications.
 *
 * This function never throws.
 *
 * @returns `true` if at least one webhook call succeeded, `false` otherwise.
 */
export async function notifyCampsiteUnavailable(
  env: Env,
  changes: AvailabilityChange[]
): Promise<boolean> {
  const filledUp = changes.filter(
    (c) => c.previouslyAvailable > 0 && c.sitesAvailable === 0
  );

  if (filledUp.length === 0) {
    return true;
  }

  const fields: EmbedField[] = filledUp.map((change) => {
    const typeTag = change.siteType ? ` (${change.siteType})` : "";
    return {
      name: change.parkName,
      value: `~~${change.campsiteName}~~${typeTag} — fully booked`,
      inline: true,
    };
  });

  const fieldChunks = chunk(fields, MAX_FIELDS_PER_EMBED);
  let anySuccess = false;

  for (const fieldGroup of fieldChunks) {
    const embed: DiscordEmbed = {
      title: "Campsites Filled Up",
      color: 0xe74c3c, // red
      fields: fieldGroup,
      footer: {
        text: "robot-geographical-society • Availability tracker",
      },
      timestamp: new Date().toISOString(),
    };

    const ok = await postWebhook(env.DISCORD_WEBHOOK_URL, { embeds: [embed] });
    if (ok) anySuccess = true;
  }

  return anySuccess;
}
