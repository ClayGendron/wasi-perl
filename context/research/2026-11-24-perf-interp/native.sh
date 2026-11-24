#!/bin/sh
# native.sh PERL DATA W WORKLOAD: one baseline workload on a native perl, with the files where
# the guest sees them. PRE names a wrapper (valgrind) to run perl under.
set -eu
perl=$1 data=$2 w=$3 work=$4
run() {
  # shellcheck disable=SC2086
  exec ${PRE:-} "$perl" "$@"
}
case $work in
hello) run -e 'print "hello\n"' ;;
sum_csv) run -F, -lane '$s += $F[2]; END { print $s }' "$data/t.csv" ;;
grep_log) run -ne '$n++ if /ERROR \d+/; END { print $n }' "$data/t.log" ;;
inplace) cp "$data/t.log" "$w/t.log"; run -pi -e 's/ERROR/WARN/g' "$w/t.log" ;;
json) run -MJSON::PP -0777 -ne 'print scalar(@{decode_json($_)}), "\n"' "$data/t.json" ;;
loop) run -e 'my $s = 0; $s += $_ * $_ for 1 .. 1_000_000; print "$s\n"' ;;
*) echo "unknown workload $work" >&2; exit 2 ;;
esac
