#!/usr/bin/env bash
# Install the obsidian-redirect page on the mini.
#
# The page (obsidian-redirect.html) is served by `tailscale serve` DIRECTLY — no
# process, no container. It just bounces the browser from a clickable https link
# to the note's obsidian:// URI (Discord won't linkify obsidian:// itself).
#
# This stages the page onto the INTERNAL disk (tailscaled can't reliably read
# /Volumes) and prints the one-time serve command. Serving a *file* needs root,
# so that last step is a `sudo` you run at the mini console (it persists across
# reboots — one time only). Re-run this script after editing the page.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)/obsidian-redirect.html"
DEST="$HOME/.local/share/obsidian-redirect/index.html"

mkdir -p "$(dirname "$DEST")"
cp "$SRC" "$DEST"
echo "staged → $DEST ($(wc -c < "$DEST") bytes)"
echo
echo "One-time, at the mini console (needs sudo — not over SSH):"
echo "    sudo tailscale serve --bg --set-path /o \"$DEST\""
echo
echo "Then verify from any tailnet device:"
echo "    https://tommys-mac-mini.tail59a169.ts.net/o?vault=home&file=Test"
