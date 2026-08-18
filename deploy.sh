#!/bin/bash
# deploy.sh — pull latest from GitHub and restart the service
#
# Usage:  ./deploy.sh
#
# Prerequisites:
#   1. The netcontrol user must be allowed to restart the service without a password:
#      Add to /etc/sudoers (via sudo visudo):
#        netcontrol ALL=(ALL) NOPASSWD: /bin/systemctl restart nettracker
#
set -e

cd "$(dirname "$0")"

echo "Pulling latest from GitHub..."
git pull

echo "Running test suite..."
python3 -m pytest tests/ -q
echo "✓ All tests passed"

echo "Applying database migrations..."
python3 migrate.py
echo "✓ Migrations applied"

echo "Restarting service..."
sudo systemctl restart nettracker

echo "✓ Deployed $(git rev-parse --short HEAD)"
