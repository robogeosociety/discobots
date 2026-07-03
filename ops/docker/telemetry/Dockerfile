# discobot-telemetry — the DuckDB telemetry sink (obsidian-automations#179).
#
# Its own image so duckdb stays out of the other bots' base. Drains the
# fleet.telemetry stream off the bus (docs/BUS.md) into one DuckDB file in the
# state volume, with a retention window. Runs as its own supervised loop —
# a stalled writer must not touch the loops it observes.
#
# Build context = ops/ :  docker build -f docker/telemetry/Dockerfile -t discobot-telemetry .
FROM discobot-base:latest
RUN pip install --no-cache-dir duckdb
CMD ["python", "/app/telemetry_sink.py"]
