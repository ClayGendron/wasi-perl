#!/bin/sh
# Fuel and wall time of study F's JSON, CSV and diff workloads, before and after the five CPAN
# distributions, with study F's own corpus (../2026-11-24-perf-libs/corpus.py), unchanged.
#
# Usage, from the worktree, with the before set copied to build/before/artifacts (the dev
# build at f44e0fd) and the after set built in build/artifacts:
#   flock /home/user/wasi-perl-wt/machine.lock sh context/research/2026-11-24-json-csv-and-diff-measured/measure.sh
#
# Before, the three pure-Perl modules are not in the library, so they are granted read-only at
# /ext on PERL5LIB, as study F granted them: a folder holding the lib/ trees of the same pinned
# tarballs that 30_library.sh unpacks (build/cpan-pure). After, nothing is granted beyond /data.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../../.." && pwd)
corpus="$root/context/research/2026-11-24-perf-libs/corpus.py"
out="$here/results"
work="$root/build/measure"
rm -rf "$work"
mkdir -p "$work/ext" "$out"
for d in "$root"/build/cpan-pure/*/lib; do cp -R "$d"/. "$work/ext/"; done
uv run python "$corpus" data "$work/data"
export CORPUS_CACHE="$work/cache"

before="json_decode json_decode+maybexs json_small json_encode jq_select csv_parsewords csv_regex
csv_pp+textcsv diff_lcs+algdiff load_jsonpp load_none"
after="json_decode json_decode+cpanel json_decode+maybexs json_small json_small+cpanel json_encode
json_encode+cpanel jq_select jq_select+cpanel csv_parsewords csv_regex csv_xs+csv_xs csv_pp+textcsv
diff_lcs+algdiff load_jsonpp load_cpanel+cpanel load_textcsv_xs+csv_xs load_none"

rm -f "$out"/*.json
# shellcheck disable=SC2086
WASI_PERL_ARTIFACTS="$root/build/before/artifacts" CORPUS_EXT="$work/ext" \
    uv run python "$corpus" fuel "$work/data" "$out/fuel-before.json" $before
# shellcheck disable=SC2086
WASI_PERL_ARTIFACTS="$root/build/artifacts" uv run python "$corpus" fuel "$work/data" "$out/fuel-after.json" $after
# shellcheck disable=SC2086
WASI_PERL_ARTIFACTS="$root/build/before/artifacts" CORPUS_EXT="$work/ext" \
    uv run python "$corpus" wall "$work/data" "$out/wall-before.json" $before
# shellcheck disable=SC2086
WASI_PERL_ARTIFACTS="$root/build/artifacts" uv run python "$corpus" wall "$work/data" "$out/wall-after.json" $after
# Native perl 5.42.3 with the same five from build/native-cpan, for the answers and a reference.
# shellcheck disable=SC2086
uv run python "$corpus" native "$work/data" "$root/build/native-install/bin/perl" \
    "$root/build/native-cpan/lib/perl5" "$out/native.json" $after
for f in "$root/build/before/artifacts/perl.wasm" "$root/build/artifacts/perl.wasm"; do
    echo "$(sha256sum "$f" | cut -d' ' -f1)  $(stat -c %s "$f")  ${f#"$root"/}"
done >"$out/sets.txt"
