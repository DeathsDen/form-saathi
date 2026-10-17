#!/bin/sh
# One-command setup and launch for Form Saathi.
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Setting up (first run only)..."
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
fi

if [ -z "$SARVAM_API_KEY" ] && { [ ! -f .streamlit/secrets.toml ] || grep -q PASTE_YOUR_KEY_HERE .streamlit/secrets.toml; }; then
  cp -n .streamlit/secrets.toml.example .streamlit/secrets.toml 2>/dev/null || true
  echo "Add your Sarvam key to .streamlit/secrets.toml (replace PASTE_YOUR_KEY_HERE), save, then run ./run.sh again."
  echo "No key yet? The app still opens. Turn on Demo mode in the sidebar."
fi

exec .venv/bin/python -m streamlit run app.py
