#!/bin/sh
# wt.sh ART DATA W WORKLOAD: one baseline workload under the wasmtime 51 CLI, with the preopens
# the runner gives (the library and /dev, read-only /data, writable /w) and an empty environment.
# WTOPTS adds wasmtime options (a profile, a trace); PERLWASM names another module.
set -eu
art=$1 data=$2 w=$3 work=$4
W=${WASMTIME:-/home/user/wasi-perl-downloads/wasmtime-v51.0.0-x86_64-linux/wasmtime}
mod=${PERLWASM:-$art/perl.wasm}
run() {
  # shellcheck disable=SC2086
  exec "$W" run -W exceptions=y ${WTOPTS:-} \
    --dir "$art/lib/perl5::/usr/local/lib/perl5" --dir "$art/dev::/dev" --dir "$data::/data" --dir "$w::/w" \
    --argv0 perl "$mod" "$@"
}
case $work in
hello) run -e 'print "hello\n"' ;;
sum_csv) run -F, -lane '$s += $F[2]; END { print $s }' /data/t.csv ;;
grep_log) run -ne '$n++ if /ERROR \d+/; END { print $n }' /data/t.log ;;
inplace) cp "$data/t.log" "$w/t.log"; run -pi -e 's/ERROR/WARN/g' /w/t.log ;;
json) run -MJSON::PP -0777 -ne 'print scalar(@{decode_json($_)}), "\n"' /data/t.json ;;
loop) run -e 'my $s = 0; $s += $_ * $_ for 1 .. 1_000_000; print "$s\n"' ;;
*) echo "unknown workload $work" >&2; exit 2 ;;
esac
