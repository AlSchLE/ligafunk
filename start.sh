#!/usr/bin/env bash
# Render start script: validate Litestream configuration, then start the app.
set -euo pipefail

required_vars=(
  DATABASE_PATH
  LITESTREAM_BUCKET
  LITESTREAM_ENDPOINT
  LITESTREAM_REGION
  LITESTREAM_REPLICA_PATH
  LITESTREAM_ACCESS_KEY_ID
  LITESTREAM_SECRET_ACCESS_KEY
)

for key in "${required_vars[@]}"; do
  if [ -z "${!key:-}" ]; then
    echo "ERROR: $key is not set in the Render environment." >&2
    exit 1
  fi
  value="${!key}"
  case "$value" in
    " "*|*" ")
      echo "WARNING: $key has leading or trailing whitespace." >&2
      ;;
  esac
done

echo "Litestream config:"
echo "  DATABASE_PATH=${DATABASE_PATH}"
echo "  LITESTREAM_BUCKET=${LITESTREAM_BUCKET}"
echo "  LITESTREAM_ENDPOINT=${LITESTREAM_ENDPOINT}"
echo "  LITESTREAM_REGION=${LITESTREAM_REGION}"
echo "  LITESTREAM_REPLICA_PATH=${LITESTREAM_REPLICA_PATH}"
echo "  LITESTREAM_ACCESS_KEY_ID length=${#LITESTREAM_ACCESS_KEY_ID}"
echo "  LITESTREAM_SECRET_ACCESS_KEY length=${#LITESTREAM_SECRET_ACCESS_KEY}"

exec ./bin/litestream replicate -config litestream.yml -exec "gunicorn --timeout 180 app:app"
