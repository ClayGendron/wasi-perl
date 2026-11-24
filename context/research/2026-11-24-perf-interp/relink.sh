#!/bin/sh
# relink.sh TREE LOG OUT.wasm [LINKER ARGS...]: link perl again in a configured, built TREE with
# the command its build log recorded, adding the given objects or flags before perlmain.o.
# LIBPERL names another libperl.a to link in place of the tree's.
# Used for prototypes that replace or wrap functions without rebuilding libperl.
set -eu
tree=$1 log=$2 out=$3; shift 3
root=$(cd "$(dirname "$0")/../../.." && pwd)
line=$(grep -m1 -- "-o perl .*perlmain.o" "$log")
# The log names the checkout it was built in; the link uses this one's wasi-cc and stubs.
line=$(echo "$line" | sed "s|/home/user/wasi-perl-wt/[a-z-]*/|$root/|g")
[ -n "${LIBPERL:-}" ] && line=$(echo "$line" | sed "s| libperl.a | $LIBPERL |")
cd "$tree"
eval "set -- $(echo "$line" | sed "s|-o perl |-o '$out' $* |")"
"$@" 2>&1 | grep -v "argument unused during compilation" || true
[ -f "$out" ]
