#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "${SCRIPT_DIR}"

mkdir -p logs
source .venv/bin/activate
exec python -u bot.py >> logs/nonebot.log 2>&1
