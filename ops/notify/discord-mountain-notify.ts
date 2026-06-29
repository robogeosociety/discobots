// Discord webhook notification module for is-the-mountain-out
//
// Integration:
// In your scheduled() handler:
// import { notifyMountainVisibility } from './discord-mountain-notify'
// ctx.waitUntil(notifyMountainVisibility(env, predictionResult))

export interface PredictionResult {
  visible: boolean;
  confidence: number;
  label: string;
  imageUrl?: string;
  timestamp?: string;
}

export interface Env {
  DISCORD_WEBHOOK_URL: string;
}

const COLOR_VISIBLE = 0x2ecc71;
const COLOR_NOT_VISIBLE = 0x95a5a6;

/**
 * Post a Discord webhook embed with the mountain visibility prediction.
 *
 * Returns true if the webhook was delivered successfully, false otherwise.
 * Never throws — Discord delivery failures are logged and swallowed so they
 * cannot crash the scheduled handler or interfere with other notifications.
 */
export async function notifyMountainVisibility(
  env: Env,
  result: PredictionResult,
): Promise<boolean> {
  const { visible, confidence, label, imageUrl, timestamp } = result;

  const confidencePct = (confidence * 100).toFixed(1);
  const ts = timestamp ?? new Date().toISOString();

  const title = visible
    ? "The mountain is out!"
    : "The mountain is not visible";

  const description = [
    `**Prediction:** ${label}`,
    `**Confidence:** ${confidencePct}%`,
    `**Timestamp:** ${ts}`,
  ].join("\n");

  const embed: Record<string, unknown> = {
    title,
    description,
    color: visible ? COLOR_VISIBLE : COLOR_NOT_VISIBLE,
    footer: {
      text: "is-the-mountain-out • Automated prediction",
    },
    timestamp: ts,
  };

  if (imageUrl) {
    embed.image = { url: imageUrl };
  }

  const payload = {
    embeds: [embed],
  };

  try {
    const response = await fetch(env.DISCORD_WEBHOOK_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const body = await response.text().catch(() => "(unreadable body)");
      console.error(
        `Discord webhook failed: ${response.status} ${response.statusText} — ${body}`,
      );
      return false;
    }

    return true;
  } catch (err) {
    console.error(
      "Discord webhook request error:",
      err instanceof Error ? err.message : String(err),
    );
    return false;
  }
}
