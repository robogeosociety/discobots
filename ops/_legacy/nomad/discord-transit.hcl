# Discord ops — transit alerts → Discord, every 5 min.
# Convention-aligned (workspace CLAUDE.md): scheduled batch → Nomad; deps via `uv run --with`.
# Self-discovers DISCORD_WEBHOOK_URL from observability/grafana/.env (Nomad agent has FDA).
#
# Runs via run-transit.sh, which sources the real OBA key from the transit_tracker app's
# .local/service.yaml (single source of truth, no secret copied).
#
# KNOWN BUG (do not run live yet): transit_discord.py calls OBA `situations-for-agency`,
# which is NOT a real OneBusAway REST method — it 404s even with a valid key + valid
# agencies (1=King County Metro, 40=Sound Transit). Needs reworking to a real
# service-alerts source before it posts anything useful.
#
#   nomad job run    nomad/discord-transit.hcl
#   nomad job periodic force discord-transit     # run now
job "discord-transit" {
  type        = "batch"
  datacenters = ["*"]

  periodic {
    cron             = "*/5 * * * *"
    prohibit_overlap = true
    time_zone        = "America/Los_Angeles"
  }

  group "post" {
    task "run" {
      driver = "raw_exec"
      config {
        command = "/bin/zsh"
        args    = ["/Volumes/dev/discord-ops/run-transit.sh"]
      }
      env {
        PATH         = "/Users/tommydoerr/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        HOME         = "/Users/tommydoerr"
        UV_CACHE_DIR = "/Volumes/dev/.caches/uv"
      }
      resources {
        cpu    = 500
        memory = 256
      }
    }
  }
}
