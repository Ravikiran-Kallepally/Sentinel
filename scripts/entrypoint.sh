#!/bin/sh
# Container entrypoint: build the platform on first start (empty volume), then serve.
# In Kubernetes the bootstrap runs as a separate Job instead (k8s/job-bootstrap.yaml) so API pods start fast.
set -e
if [ ! -f "$SENTINEL_HOME/warehouse.duckdb" ] && [ "${SKIP_BOOTSTRAP:-false}" != "true" ]; then
  echo "No platform data found in $SENTINEL_HOME - running bootstrap (about a minute)..."
  python -m sentinel.cli bootstrap --agent 4 || echo "bootstrap finished with a failing gate; serving anyway for the demo"
fi
# One worker: the demo keeps the live replay cursor and semantic cache in process memory and uses SQLite.
# Scaling out means moving that state to Redis/Postgres first (docs/next-steps.md) - a good exercise.
exec uvicorn sentinel.api.main:app --host 0.0.0.0 --port 8000 --workers "${WORKERS:-1}"
