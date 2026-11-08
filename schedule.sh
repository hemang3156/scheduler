#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
#  📅  Autonomous Calendar Scheduler — Quick Launcher
# ─────────────────────────────────────────────────────────
#  Just run:  ./schedule.sh
#  This launches the interactive guided wizard.
#  You can also pass arguments:  ./schedule.sh "Gym 7am" --dry-run
# ─────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")"

# Check if uv is available
if ! command -v uv &> /dev/null; then
    echo ""
    echo "❌  'uv' is not installed."
    echo "    Run ./setup.sh first, or install uv manually:"
    echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo ""
    exit 1
fi

exec uv run scheduler "$@"
