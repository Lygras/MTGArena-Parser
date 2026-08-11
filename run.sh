#!/usr/bin/env bash
# Snapshot both Arena logs into timestamped archives under archive/, then
# resolve card names and rebuild report.txt across every snapshot.
#
# Safe to run on a schedule: each log is content-hashed and only parsed when
# it has changed since the last run. Log rotation (Player.log becoming
# Player-prev.log) is caught by the hash, not the filename, so a rotated but
# unchanged log is never archived twice.
set -euo pipefail

cd "$(dirname "$0")"

PY=./.venv/bin/python
LOGDIR="${MTGA_LOG_DIR:-$(ls -d /mnt/c/Users/*/AppData/LocalLow/"Wizards Of The Coast"/MTGA 2>/dev/null | head -n1 || true)}"
ARCHIVE=archive
SEEN="$ARCHIVE/seen_logs.txt"   # one "<sha256>  <snapshot dir>" per line
KEEP_RAW="${MTGA_KEEP_RAW:-1}"  # gzip the raw log into each snapshot

if [[ ! -x "$PY" ]]; then
  echo "No venv found. Run ./setup.sh first." >&2
  exit 1
fi

if [[ -z "$LOGDIR" || ! -d "$LOGDIR" ]]; then
  echo "Arena log directory not found. Set MTGA_LOG_DIR." >&2
  exit 1
fi

mkdir -p "$ARCHIVE"
touch "$SEEN"

new_snapshots=0

snapshot() {
  local log="$1" label="$2"
  [[ -f "$log" ]] || return 0

  local hash
  hash=$(sha256sum "$log" | cut -d' ' -f1)
  if grep -q "^$hash " "$SEEN"; then
    echo "== $label: already archived, skipping =="
    return 0
  fi

  # Stamp with the log's mtime (end of the Arena session), not the run time.
  local stamp dir
  stamp=$(date -r "$log" +%Y%m%d-%H%M%S)
  dir="$ARCHIVE/${stamp}_${label}_${hash:0:8}"

  echo "== $label -> $dir =="
  mkdir -p "$dir"
  "$PY" local_parse.py "$log" -o "$dir" | tail -n 12
  if [[ "$KEEP_RAW" = 1 ]]; then
    gzip -c "$log" > "$dir/$label.log.gz"
  fi

  # Recorded only after a successful parse, so a failed run retries next time.
  echo "$hash  $dir" >> "$SEEN"
  new_snapshots=1
}

snapshot "$LOGDIR/Player.log" player
snapshot "$LOGDIR/Player-prev.log" player-prev

if [[ "$new_snapshots" -eq 0 ]]; then
  echo "No new log content; report.txt left as is."
  exit 0
fi

echo "== resolving card names =="
"$PY" resolve_cards.py

# Write to a temp file and move into place so a mid-run failure
# can't leave a truncated report.txt behind.
echo "== writing report.txt =="
"$PY" report.py > report.txt.tmp
mv report.txt.tmp report.txt
echo "report.txt: $(wc -l < report.txt) lines"
