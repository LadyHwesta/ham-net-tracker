#!/bin/bash
set -e
cd /opt/netcontrol
git pull
sudo systemctl restart nettracker
echo "✓ Deployed $(git rev-parse --short HEAD)"
