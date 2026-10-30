#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then
  exec .venv/bin/python apps/api/run_desktop.py "$@"
elif [ -x .venv-ci/bin/python ]; then
  exec .venv-ci/bin/python apps/api/run_desktop.py "$@"
else
  echo 'Install Python dependencies into .venv first. See docs/desktop-sqlite-foundation.md' >&2
  exit 1
fi
