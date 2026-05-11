#!/bin/bash
# setup.sh — Zero Human Touch Pipeline environment setup script.
#
# Usage:
#   chmod +x setup.sh
#   ./setup.sh
#
# What this script does:
#   1. Verifies Python 3.8+ is available.
#   2. Creates a virtual environment in ./venv/.
#   3. Activates it and installs Python dependencies from requirements.txt.
#   4. Installs the Playwright Chromium browser binary.
#   5. Verifies Node.js is available (required for Jest tests).
#   6. Prints next-steps instructions.

set -e  # Exit immediately if any command fails.

echo "============================================================"
echo " Zero Human Touch Pipeline — Setup"
echo "============================================================"

# ---------------------------------------------------------------------------
# 1. Check Python 3.8+
# ---------------------------------------------------------------------------
echo ""
echo "[1/5] Checking Python version..."

PYTHON_BIN=""
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
        VERSION=$("$candidate" --version 2>&1 | awk '{print $2}')
        MAJOR=$(echo "$VERSION" | cut -d. -f1)
        MINOR=$(echo "$VERSION" | cut -d. -f2)
        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 8 ]; then
            PYTHON_BIN="$candidate"
            echo "    Found: $PYTHON_BIN $VERSION"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "ERROR: Python 3.8 or higher is required but was not found."
    echo "       Install it from https://www.python.org/downloads/ and retry."
    exit 1
fi

# ---------------------------------------------------------------------------
# 2. Create virtual environment
# ---------------------------------------------------------------------------
echo ""
echo "[2/5] Creating virtual environment in ./venv/..."

if [ -d "venv" ]; then
    echo "    ./venv/ already exists — skipping creation."
else
    "$PYTHON_BIN" -m venv venv
    echo "    Virtual environment created."
fi

# ---------------------------------------------------------------------------
# 3. Activate and install Python dependencies
# ---------------------------------------------------------------------------
echo ""
echo "[3/5] Installing Python dependencies from requirements.txt..."

# shellcheck disable=SC1091
source venv/bin/activate

pip install --upgrade pip --quiet
pip install -r requirements.txt

echo "    Python dependencies installed."

# ---------------------------------------------------------------------------
# 4. Install Playwright Chromium
# ---------------------------------------------------------------------------
echo ""
echo "[4/5] Installing Playwright Chromium browser..."

playwright install chromium

echo "    Playwright Chromium installed."

# ---------------------------------------------------------------------------
# 5. Check Node.js
# ---------------------------------------------------------------------------
echo ""
echo "[5/5] Checking Node.js availability (required for Jest tests)..."

if command -v node &>/dev/null; then
    NODE_VERSION=$(node --version)
    echo "    Found: node $NODE_VERSION"
else
    echo "WARNING: Node.js was not found on PATH."
    echo "         Jest tests will fail without Node.js."
    echo "         Install it from https://nodejs.org/ and ensure it is on PATH."
fi

if command -v npm &>/dev/null; then
    NPM_VERSION=$(npm --version)
    echo "    Found: npm $NPM_VERSION"
else
    echo "WARNING: npm was not found on PATH."
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo " Setup complete."
echo ""
echo " Next steps:"
echo "   1. Copy .env.example to .env and fill in your credentials:"
echo "        JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY"
echo "        ANTHROPIC_API_KEY"
echo "        GITHUB_TOKEN, GITHUB_REPO"
echo "        VERCEL_TOKEN, VERCEL_PROJECT_ID, VERCEL_ORG_ID,"
echo "        VERCEL_PROJECT_NAME"
echo "        EMAIL_FROM, EMAIL_TO, SMTP_HOST, SMTP_PORT,"
echo "        SMTP_USER, SMTP_PASSWORD"
echo ""
echo "   2. Start the pipeline:"
echo "        source venv/bin/activate && python main.py"
echo "============================================================"
