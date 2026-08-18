#!/bin/bash
# deploy.sh — pull latest from GitHub and restart the service
#
# Usage:  ./deploy.sh
#
# Supports running multiple instances of this app from separate checkouts on
# one server (e.g. production + a demo site) — each instance's .env sets its
# own SYSTEMD_SERVICE (and PORT, used by the systemd unit's ExecStart line),
# so the same deploy.sh works unmodified for every instance. Falls back to
# "nettracker" if SYSTEMD_SERVICE isn't set. See "Deployment" in README.md.
#
# Prerequisites:
#   1. The netcontrol user must be allowed to restart the service without a
#      password. Add to /etc/sudoers (via sudo visudo), substituting your
#      SYSTEMD_SERVICE value:
#        netcontrol ALL=(ALL) NOPASSWD: /bin/systemctl restart nettracker
#
set -e

cd "$(dirname "$0")"

# Read SYSTEMD_SERVICE from .env without sourcing the whole file — values in
# there aren't guaranteed to be safe shell syntax.
SYSTEMD_SERVICE=$(grep -E '^SYSTEMD_SERVICE=' .env 2>/dev/null | tail -1 | cut -d= -f2-)
SYSTEMD_SERVICE="${SYSTEMD_SERVICE:-nettracker}"

# Use this checkout's own virtualenv if one exists, rather than whatever
# `python3` happens to resolve to on the caller's PATH -- each checkout
# (e.g. production vs. a demo instance) has its own venv with its own
# installed deps, and deploy.sh shouldn't depend on the shell that invoked
# it having activated the right one.
PYTHON=python3
if [ -x venv/bin/python3 ]; then
  PYTHON=venv/bin/python3
fi

echo "Pulling latest from GitHub..."
git pull

echo "Running test suite..."
"$PYTHON" -m pytest tests/ -q
echo "✓ All tests passed"

echo "Applying database migrations..."
"$PYTHON" migrate.py
echo "✓ Migrations applied"

echo "Restarting $SYSTEMD_SERVICE..."
sudo systemctl restart "$SYSTEMD_SERVICE"

echo "✓ Deployed $(git rev-parse --short HEAD) to $SYSTEMD_SERVICE"
