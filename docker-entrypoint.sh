#!/bin/sh

set -u

run_watcher() {
  echo "Starting live Cinema City check."
  if python src/watch.py; then
    echo "Live Cinema City check completed successfully."
  else
    status=$?
    echo "ERROR: Watcher cycle had an error (exit code $status); published data remains available and notifications will retry." >&2
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

exec python -m src.serve_data
