#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
#  📅  Autonomous Calendar Scheduler — First-Time Setup
# ─────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")"

BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
CYAN="\033[36m"
RESET="\033[0m"

echo ""
echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${CYAN}${BOLD}║           📅  SCHEDULER — FIRST-TIME SETUP  📅             ║${RESET}"
echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════════════════════════╝${RESET}"
echo ""

PASS=0
FAIL=0

# ── Step 1: Check Python ────────────────────────────────────────────
echo -e "${BOLD}[1/5] Checking Python...${RESET}"
if command -v python3 &> /dev/null; then
    PY_VERSION=$(python3 --version 2>&1)
    echo -e "  ${GREEN}✓${RESET} $PY_VERSION found."
    PASS=$((PASS + 1))
else
    echo -e "  ${RED}✗${RESET} Python 3 not found. Install Python 3.11 or newer."
    echo "    https://www.python.org/downloads/"
    FAIL=$((FAIL + 1))
fi

# ── Step 2: Check/Install uv ────────────────────────────────────────
echo ""
echo -e "${BOLD}[2/5] Checking uv package manager...${RESET}"
if command -v uv &> /dev/null; then
    UV_VERSION=$(uv --version 2>&1)
    echo -e "  ${GREEN}✓${RESET} uv $UV_VERSION found."
    PASS=$((PASS + 1))
else
    echo -e "  ${YELLOW}!${RESET} uv not found. Installing..."
    if curl -LsSf https://astral.sh/uv/install.sh | sh; then
        export PATH="$HOME/.local/bin:$PATH"
        echo -e "  ${GREEN}✓${RESET} uv installed successfully."
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}✗${RESET} Could not install uv automatically."
        echo "    Install it manually: https://github.com/astral-sh/uv#installation"
        FAIL=$((FAIL + 1))
    fi
fi

# ── Step 3: Install dependencies ────────────────────────────────────
echo ""
echo -e "${BOLD}[3/5] Installing project dependencies...${RESET}"
if uv sync --extra dev 2>&1; then
    echo -e "  ${GREEN}✓${RESET} All dependencies installed."
    PASS=$((PASS + 1))
else
    echo -e "  ${RED}✗${RESET} Dependency installation failed. Check error messages above."
    FAIL=$((FAIL + 1))
fi

# ── Step 4: Check credentials.json ──────────────────────────────────
echo ""
echo -e "${BOLD}[4/5] Checking Google Calendar credentials...${RESET}"
if [ -f "credentials.json" ]; then
    echo -e "  ${GREEN}✓${RESET} credentials.json found."
    PASS=$((PASS + 1))
    # Check for token.json (already authorized)
    if [ -f "token.json" ]; then
        echo -e "  ${GREEN}✓${RESET} token.json found — already authorized."
    else
        echo -e "  ${YELLOW}!${RESET} token.json not found."
        echo "    Your browser will open for Google sign-in on first run."
    fi
else
    echo -e "  ${RED}✗${RESET} credentials.json not found."
    echo ""
    echo -e "  ${BOLD}How to get it:${RESET}"
    echo "  1. Go to https://console.cloud.google.com/"
    echo "  2. Create a project (or use an existing one)"
    echo "  3. Enable the 'Google Calendar API'"
    echo "  4. Go to APIs & Services → Credentials"
    echo "  5. Click 'Create Credentials' → 'OAuth client ID'"
    echo "  6. Choose 'Desktop application'"
    echo "  7. Download the JSON file"
    echo "  8. Save it as 'credentials.json' in this folder:"
    echo "     $(pwd)/credentials.json"
    echo ""
    FAIL=$((FAIL + 1))
fi

# ── Step 5: Check Gemini API key ────────────────────────────────────
echo ""
echo -e "${BOLD}[5/5] Checking Gemini API key...${RESET}"
if [ -n "${GEMINI_API_KEY:-}" ]; then
    echo -e "  ${GREEN}✓${RESET} GEMINI_API_KEY environment variable is set."
    PASS=$((PASS + 1))
elif [ -f "gemini_api_key.txt" ]; then
    echo -e "  ${GREEN}✓${RESET} gemini_api_key.txt found."
    PASS=$((PASS + 1))
else
    echo -e "  ${RED}✗${RESET} Gemini API key not found."
    echo ""
    echo -e "  ${BOLD}How to get it:${RESET}"
    echo "  1. Go to https://aistudio.google.com/apikey"
    echo "  2. Click 'Create API key'"
    echo "  3. Copy the key, then do ONE of:"
    echo ""
    echo "     Option A — Save to file (easiest):"
    echo "       echo 'your-key-here' > gemini_api_key.txt"
    echo ""
    echo "     Option B — Set environment variable:"
    echo "       export GEMINI_API_KEY='your-key-here'"
    echo "       (Add this line to your ~/.bashrc to make it permanent)"
    echo ""
    FAIL=$((FAIL + 1))
fi

# ── Summary ─────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ "$FAIL" -eq 0 ]; then
    echo -e "${GREEN}${BOLD}  ✓ All checks passed! You're ready to go.${RESET}"
    echo ""
    echo -e "  ${BOLD}To start scheduling, run:${RESET}"
    echo -e "    ${CYAN}./schedule.sh${RESET}"
    echo ""
else
    echo -e "${YELLOW}${BOLD}  ⚠  $FAIL check(s) need attention. Fix them and re-run this script.${RESET}"
    echo ""
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
