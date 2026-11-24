#!/bin/sh
# The probe under the wasmtime CLI: each kind `runs` times, the median of the guest's own
# ns per call, as JSON lines. With TIMEOUT set, `-W timeout=$TIMEOUT` is passed, which makes
# the CLI leave allow_blocking_current_thread off.
#   cli.sh <wasmtime> <probe.wasm> <data> <n> <runs> <kinds, comma separated>
set -eu
wt=$1 probe=$2 data=$3 n=$4 runs=$5 kinds=$6
extra=""
[ -n "${TIMEOUT:-}" ] && extra="-W timeout=$TIMEOUT"
for kind in $(echo "$kinds" | tr , ' '); do
  for r in $(seq "$runs"); do
    # shellcheck disable=SC2086
    "$wt" run $extra --dir "$data::/d" "$probe" "$kind" "$n"
  done | python3 -c '
import json, statistics, sys
v = [float(l.rsplit("=", 1)[1]) / 1000 for l in sys.stdin]
print(json.dumps({"kind": sys.argv[1], "n": int(sys.argv[2]), "runs": len(v), "arm": sys.argv[3],
                  "us_per_call_median": round(statistics.median(v), 3),
                  "us_per_call_min": round(min(v), 3), "us_per_call_max": round(max(v), 3)}))
' "$kind" "$n" "cli${TIMEOUT:+-timeout}"
done
