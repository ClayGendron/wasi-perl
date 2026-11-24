#!/usr/bin/env bash
# The whole timed run. Every timed script holds the machine lock, one script per hold.
# Usage: measure.sh SCRATCH SESSION [STEP ...]   (SCRATCH holds mdata/, the Monty venv, the caches)
# Steps: perl monty native stdin hostdata first idle; paced cap limits phases; and flag, only when
# named, with PATCHED_WT set. none named runs them all.
set -euo pipefail
S=$1; L=$2; shift 2
STEPS=${*:-perl monty native stdin hostdata first idle}
E=$(cd "$(dirname "$0")" && pwd); R=$E/results; mkdir -p "$R"
LOCK=/home/user/wasi-perl-wt/machine.lock
# The runner of dev 8b81f84, spec 014 merged. Session s1's warm calls (perl-calls-s1.json) ran instead
# from /home/user/wasi-perl-wt/014-the-runners-fixed-cost/.venv at 72738d2, since cleared; its src/
# differs from 8b81f84 by one comment in _cache.py.
PERL_PY=/home/user/wasi-perl-wt/monty/.venv/bin/python
MONTY_PY=$S/venvs/monty-venv/bin/python
MONTY_BIN=$S/venvs/monty-venv/bin/monty
NATIVE_PERL=/home/user/wasi-perl-wt/perf-base/build/native-install/bin/perl
export WASI_PERL_ARTIFACTS=/home/user/wasi-perl-wt/perf-base/build/artifacts
export WASI_PERL_CACHE=$S/wpcache
D=$S/mdata
cd "$S"
lk() { flock "$LOCK" "$@"; }
for step in $STEPS; do
  case $step in
    perl) lk "$PERL_PY" "$E/bench_perl.py" calls "$D" "$R/perl-calls-$L.json" ;;
    monty) for m in checkout session fresh; do
        lk "$MONTY_PY" -I "$E/bench_monty.py" calls $m "$D" "$R/monty-calls-$m-$L.json"
      done ;;
    native) lk "$MONTY_PY" -I "$E/native.py" times "$NATIVE_PERL" "$MONTY_BIN" "$D" "$R/native-times-$L.json" ;;
    stdin) lk "$PERL_PY" "$E/bench_perl.py" stdin "$D" "$R/perl-stdin-$L.json" ;;
    hostdata) lk "$MONTY_PY" -I "$E/bench_monty.py" hostdata "$D" "$R/monty-hostdata-$L.json" ;;
    first) : > "$R/perl-first-$L.jsonl"; : > "$R/monty-first-$L.jsonl"
      for i in 1 2 3 4 5 6 7; do
        lk "$PERL_PY" "$E/bench_perl.py" first "$WASI_PERL_CACHE" "$D" >> "$R/perl-first-$L.jsonl"
        lk "$MONTY_PY" -I "$E/bench_monty.py" first "$D" >> "$R/monty-first-$L.jsonl"
      done ;;
    flag) for arm in off on; do
        lk "$PERL_PY" "$E/bench_perl.py" flag "$PATCHED_WT" "$D" "$R/perl-flag-$arm-$L.json" $([ $arm = on ] && echo --blocking)
      done ;;
    paced) lk "$PERL_PY" "$E/bench_perl.py" paced "$D" "$R/perl-paced-$L.json"
      for m in fresh checkout; do
        lk "$MONTY_PY" -I "$E/bench_monty.py" paced $m "$D" "$R/monty-paced-$m-$L.json"
      done ;;
    cap) lk "$MONTY_PY" -I "$E/bench_monty.py" cap "$D" "$R/monty-cap-$L.json" ;;
    limits) lk "$MONTY_PY" -I "$E/bench_monty.py" limits "$D" "$R/monty-limits-$L.json" ;;
    phases) # study A's per-phase profile of a bare call, for the cost of the work around the guest
      for sc in bare ro; do
        lk "$PERL_PY" "$E/../2026-11-24-perf-runner/phases.py" $sc 100 "$R/perl-phases-$sc-$L.json"
      done ;;
    idle) lk "$PERL_PY" "$E/bench_perl.py" idle "$R/perl-idle-$L.json"
      lk "$MONTY_PY" -I "$E/bench_monty.py" idle "$R/monty-idle-$L.json" ;;
  esac
done
