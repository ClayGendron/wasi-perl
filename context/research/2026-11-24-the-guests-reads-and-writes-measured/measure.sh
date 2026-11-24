#!/bin/sh
# The whole measurement, from the worktree's root, with dev's build copied to build/before/artifacts
# before the change was built and the change's own build in build/artifacts. DATA is a scratch
# folder. Counts run anywhere; times run under the machine lock, in three interleaved rounds.
set -eu
E=context/research/2026-11-24-the-guests-reads-and-writes-measured
DATA=${1:?usage: measure.sh DATA}
V010=${V010:-/home/user/wasi-perl-wt/perf-base/build/artifacts}  # built from 0.1.0's recipes
W=$PWD/build/tools/wasmtime-v51.0.0-x86_64-linux/wasmtime
[ -f "$DATA/t.log" ] || uv run python $E/measure.py data "$DATA"
for v in before:build/before/artifacts after:build/artifacts v010:$V010; do
    uv run python $E/measure.py fuel "${v#*:}" "$DATA" "$E/results/fuel-${v%%:*}.json"
done
for v in before:build/before/artifacts after:build/artifacts; do
    uv run python $E/measure.py calls "${v#*:}" "$DATA" "$E/results/calls-${v%%:*}.json" "$W"
done
for r in 1 2 3; do
    for v in before:build/before/artifacts after:build/artifacts; do
        flock /home/user/wasi-perl-wt/machine.lock uv run python $E/measure.py time "${v#*:}" "$DATA" \
            "$E/results/time-r$r-${v%%:*}.json" hello sum_csv grep_log inplace count_lines many_files loop
    done
done
