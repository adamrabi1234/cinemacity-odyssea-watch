#!/bin/sh

set -u

run_watcher() {
  echo "Starting live Cinema City check."
  if python src/watch.py; then
    echo "Live Cinema City check completed successfully."
  else
    status=$?
    echo "ERROR: Live Cinema City check failed with exit code $status; keeping the last good snapshot." >&2
  fi
}

python src/schedule.py >/dev/null || exit 2
run_watcher

(
  while true; do
    interval="$(python src/schedule.py)" || exit 2
    echo "Next live Cinema City check in ${interval} seconds."
    sleep "$interval"
    run_watcher
  done
) &

exec python src/serve_data.py
