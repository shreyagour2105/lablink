#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

DJANGO_SECRET_KEY=${DJANGO_SECRET_KEY:-}
if [[ -z "${DJANGO_SECRET_KEY//[[:space:]]/}" ]]; then
  echo "Set DJANGO_SECRET_KEY for the target Vercel environment before building LabLink." >&2
  exit 1
fi

npm run build
mkdir -p vercel-static/static
cp frontend/static/lablink.css frontend/dist/app.js frontend/dist/faculty.js vercel-static/static/
