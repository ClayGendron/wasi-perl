#!/usr/bin/env bash
# The whole timed run. Every timed script holds the machine lock, one script per hold.
# Usage: measure.sh SCRATCH SESSION [STEP ...]   (SCRATCH holds mdata/, the Monty venv, the caches)
# Steps: perl monty native stdin hostdata first idle (flag, only when named: needs PATCHED_WT); none named runs them all.
set -euo pipefail
S=$1; L=$2; shift 2
STEPS=${*:-perl monty native stdin hostdata first idle}
E=$(cd "$(dirname "$0")" && pwd); R=$E/results; mkdir -p "$R"
LOCK=/home/user/wasi-perl-wt/machine.lock
PERL_PY=/home/user/wasi-perl-wt/monty/.venv/bin/python   # the runner of dev 8b81f84: spec 014 merged
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
    idle) lk "$PERL_PY" "$E/bench_perl.py" idle "$R/perl-idle-$L.json"
      lk "$MONTY_PY" -I "$E/bench_monty.py" idle "$R/monty-idle-$L.json" ;;
  esac
done
