#!/usr/bin/env python3
"""obsidian-redirect — tailnet https → obsidian:// deep-link bridge.

Discord only makes http(s) URLs clickable, never custom schemes like
obsidian://. This serves a clickable https link (exposed via `tailscale serve`)
that bounces the browser to the obsidian:// URI so the note opens in Obsidian.

    GET /o?vault=home&file=Trips%2FLAX%20Summer%20Break
      → 200 HTML that redirects the browser to
        obsidian://open?vault=home&file=Trips%2FLAX%20Summer%20Break

The query string is passed through verbatim — it's exactly the query an
`obsidian://open` URL takes — so callers build the same percent-encoded path
they'd use for the native URI, just under the redirector's https origin.

Stateless, stdlib-only, no secrets. Runs as the discobot-redirect container.
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8099

HELP = (
    b"obsidian-redirect: GET /o?vault=<vault>&file=<percent-encoded path>\n"
    b"e.g. /o?vault=home&file=Trips%2FLAX%20Summer%20Break\n"
)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Everything after the first '?' is the obsidian query, verbatim
        # (tailscale serve may or may not keep the /o mount prefix — we don't
        # care, we only read the query).
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        if not query:
            self.send_response(400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(HELP)
            return

        target = "obsidian://open?" + query
        # json.dumps → a safe JS/HTML string literal (the query is already
        # percent-encoded, but this defends against anything odd).
        js = json.dumps(target)
        href = target.replace("&", "&amp;").replace('"', "&quot;")
        body = (
            "<!doctype html><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width'>"
            "<title>Opening in Obsidian…</title>"
            f"<script>location.replace({js})</script>"
            f"<p>Opening <a href=\"{href}\">in Obsidian</a>…</p>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # quiet; container logs stay clean
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
