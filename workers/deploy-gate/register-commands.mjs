// Register the /deploy slash command (global). Run by CI after each deploy.
// env: DISCORD_BOT_TOKEN, DISCORD_APPLICATION_ID
const appId = process.env.DISCORD_APPLICATION_ID;
const res = await fetch(`https://discord.com/api/v10/applications/${appId}/commands`, {
  method: "PUT",
  headers: {
    authorization: `Bot ${process.env.DISCORD_BOT_TOKEN}`,
    "content-type": "application/json",
  },
  body: JSON.stringify([
    {
      name: "deploy",
      description: "Approve or reject a pending GitHub deployment",
      options: ["approve", "reject"].map((name) => ({
        type: 1, // SUB_COMMAND
        name,
        description: `${name} a pending deployment`,
        options: [
          { type: 3, name: "repo", description: "repository (org fixed)", required: true },
          { type: 3, name: "run_id", description: "workflow run id", required: true },
          { type: 3, name: "env", description: "environment (default: production)", required: false },
        ],
      })),
    },
  ]),
});
if (!res.ok) {
  console.error("command registration failed", res.status, await res.text());
  process.exit(1);
}
console.log("registered /deploy (global)");
