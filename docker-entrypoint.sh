#!/bin/sh

set -u

interval="${WATCH_INTERVAL_SECONDS:-900}"

case "$interval" in
  ''|*[!0-9]*)
    echo "ERROR: WATCH_INTERVAL_SECONDS must be a positive integer." >&2
    exit 2
    ;;
esac

if [ "$interval" -le 0 ]; then
  echo "ERROR: WATCH_INTERVAL_SECONDS must be greater than zero." >&2
  exit 2
fi

run_watcher() {
  echo "Starting live Cinema City check."
  if python src/watch.py; then
    echo "Live Cinema City check completed successfully."
  else
    status=$?
    echo "ERROR: Live Cinema City check failed with exit code $status; keeping the last good snapshot." >&2
  fi
}

run_watcher

(
  while sleep "$interval"; do
    run_watcher
  done
) &

exec python src/serve_data.py
