#!/bin/sh
# Link perl.wasm a last time, with the index of the library 30_library.sh laid out, and write the
# list the runner checks the set against.
#
# The index is made from the library, and the library from installperl, which needs the tree's
# linked perl; so 20_target.sh links perl with the empty index first and this links it again.
# The index is compiled from build/index/lib-index.c, written by lib_index.sh, and named in the
# perl rule's libs before the configured ones: the linker then never takes the empty index from
# the descriptor table's archive. Every other input of the rule is up to date, so make runs only
# the link. Its result is build/link/perl.wasm, and the set's copy is build/artifacts/perl.wasm.
#
# build/artifacts/SHA256SUMS is "<sha256>  <path>" for every file of the set but itself
# (perl.wasm, MANIFEST, lib/ and dev/), written last, after every link.
. "$(dirname "$0")/lib.sh"
LOG="$LOGS/40_link.log"
SRC="$BUILD/target"
ART="$BUILD/artifacts"
INDEX="$BUILD/index"
LINK="$BUILD/link"
STAMP="$BUILD/.link.stamp"

[ -f "$BUILD/.library.stamp" ] && [ -f "$ART/MANIFEST" ] && [ -d "$ART/lib/perl5" ] \
    || fail "the library is missing; run scripts/30_library.sh"
[ -f "$SRC/perl" ] && [ -f "$SRC/config.sh" ] || fail "build/target is missing; run scripts/20_target.sh"
stamp=$(stamp_of "$0" "$(dirname "$0")/lib_index.sh" "$(dirname "$0")/lib.sh" "$BUILD/.library.stamp")
if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$stamp" ] && [ -f "$ART/SHA256SUMS" ] \
    && [ -f "$ART/perl.wasm" ]; then
    echo "40_link: up to date"
    exit 0
fi

: >"$LOG"
rm -rf "$INDEX" "$LINK" "$ART/perl.wasm" "$ART/SHA256SUMS" "$STAMP"

# The index of the library as it is laid out.
mkdir -p "$INDEX"
run "$LOG" sh "$(dirname "$0")/lib_index.sh" "$ART/lib/perl5" "$INDEX/lib-index.c"
run "$LOG" "$ROOT/recipes/wasi-cc" -O2 -Wall -Wextra -Werror -c "$INDEX/lib-index.c" -o "$INDEX/lib-index.o"

# The relink: the perl rule alone, with the index first in its libs. One make, with no -j:
# nothing but the link is out of date, so there is nothing to run beside it.
libs=$(sed -n "s/^libs='\(.*\)'\$/\1/p" "$SRC/config.sh")
[ -n "$libs" ] || fail "build/target/config.sh names no libs"
cd "$SRC"
rm -f perl
run "$LOG" make perl "libs=$INDEX/lib-index.o $libs"
[ -f perl ] || fail "make did not link perl (see $LOG)"
mkdir -p "$LINK"
cp perl "$LINK/perl.wasm"

# Into the set.
cp "$LINK/perl.wasm" "$ART/perl.wasm"

# SHA256SUMS, last.
cd "$ART"
listed . ! -path ./SHA256SUMS >"$BUILD/SHA256SUMS.tmp"
mv "$BUILD/SHA256SUMS.tmp" SHA256SUMS
echo "$stamp" >"$STAMP"
echo "40_link: ok ($ART/perl.wasm, $(wc -l <SHA256SUMS | tr -d ' ') files in $ART/SHA256SUMS)"
