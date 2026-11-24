#!/usr/bin/env bash
# Counts, not times: guest fuel, linear memory, worker peak RSS, native instructions under valgrind.
# Run under the lock too, so that they do not disturb another agent's timed run.
# Usage: counts.sh SCRATCH
set -euo pipefail
S=$1
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
lk "$PERL_PY" "$E/bench_perl.py" out "$D" "$R/perl-stdout.json"
lk "$MONTY_PY" -I "$E/bench_monty.py" out "$D" "$R/monty-stdout.json"
lk "$PERL_PY" "$E/bench_perl.py" fuel "$D" "$R/perl-fuel.json"
lk "$PERL_PY" "$E/bench_perl.py" memory "$D" "$R/perl-memory.json"
lk "$MONTY_PY" -I "$E/bench_monty.py" memory "$D" "$R/monty-memory.json"
lk "$MONTY_PY" -I "$E/native.py" instr "$NATIVE_PERL" "$MONTY_BIN" "$D" "$R/native-instr.json"
