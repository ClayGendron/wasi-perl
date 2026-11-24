#!/bin/sh
# lib_index.sh <library folder> <out.c>: writes the library index of the folder as one C source.
#
# The index is every file and folder under the folder, by its path relative to it, in the order
# of the names with A to Z made a to z, compared in bytes (LC_ALL=C sort of the lowercased name),
# the order the descriptor table's binary search uses. Each name keeps its own case. With them
# go their count and a marker: "wasi-perl-lib-index-sha256:" and the SHA-256 of the names, one
# per line, in that order. recipes/include/wasi-perl-lib-index.h declares the structure.
#
# Every name must be a plain C string literal, so a name holding a blank, a control character, a
# quote or a backslash is refused rather than escaped. Anything that is neither a file nor a
# folder is refused, and so is an empty folder: a name is a folder exactly when another name lies
# below it, so an empty one would read as a file. Two names that differ only in case are refused:
# a host whose names ignore case could not hold both, and the search finds one name for each.
. "$(dirname "$0")/lib.sh"

[ $# -eq 2 ] || fail "usage: lib_index.sh <library folder> <out.c>"
[ -d "$1" ] || fail "$1 is not a folder"
out_dir=$(cd "$(dirname "$2")" && pwd) || fail "the folder of $2 does not exist"
out="$out_dir/$(basename "$2")"
cd "$1"

[ -z "$(find . ! -type f ! -type d)" ] || fail "$1 holds something that is neither a file nor a folder"
[ -z "$(find . -mindepth 1 -type d -empty)" ] || fail "$1 holds an empty folder"
# grep below reads a line at a time, so a newline is looked for by find itself.
[ -z "$(find . -mindepth 1 -name "$(printf '*\n*')")" ] || fail "a name in $1 holds a newline"
if find . -mindepth 1 | grep -q "[[:space:][:cntrl:]\\\\'\"]"; then
    fail "a name in $1 holds a blank, a control character, a quote or a backslash"
fi
same=$(find . -mindepth 1 | sed 's|^\./||' | LC_ALL=C awk '{ print tolower($0) }' | LC_ALL=C sort | uniq -d)
[ -z "$same" ] || fail "names in $1 differ only in case: $(printf '%s\n' "$same" | head -n 1)"
tab=$(printf '\t')
names=$(find . -mindepth 1 | sed 's|^\./||' | LC_ALL=C awk '{ print tolower($0) "\t" $0 }' \
    | LC_ALL=C sort -t "$tab" -k1,1 | cut -f2)
[ -n "$names" ] || fail "$1 holds nothing to index"
count=$(printf '%s\n' "$names" | wc -l | tr -d ' ')
hex=$(printf '%s\n' "$names" | sha256sum | cut -d' ' -f1)

{
    echo "/* The library index, written by scripts/lib_index.sh: every file and folder of the"
    echo " * library, relative to the folder the runner grants at /usr/local/lib/perl5. */"
    echo '#include "wasi-perl-lib-index.h"'
    echo
    echo "static const char *const names[] = {"
    printf '%s\n' "$names" | sed 's/.*/    "&",/'
    echo "};"
    echo
    echo "const struct wasi_perl_lib_index wasi_perl_lib_index = {"
    echo "    $count,"
    echo "    names,"
    echo "    \"wasi-perl-lib-index-sha256:$hex\","
    echo "};"
} >"$out.tmp"
mv "$out.tmp" "$out"
