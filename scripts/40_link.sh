#!/bin/sh
# Link perl.wasm a last time, with the index of the library 30_library.sh laid out, and write the
# list the runner checks the set against.
#
# The index is made from the library, and the library from installperl, which needs the tree's
# linked perl; so 20_target.sh links perl with the empty index first and this links it again.
# The index is compiled from build/index/lib-index.c, written by lib_index.sh, and named in the
# perl rule's libs before the configured ones: the linker then never takes the empty index from
# the descriptor table's archive. Every other input of the rule is up to date, so make runs only
# the link. Its result is build/link/perl.wasm, wasm-ld's own output, kept to compare with.
#
# binaryen's wasm-opt -O3 then runs once over it, and its output is build/artifacts/perl.wasm:
# about 2 to 3% fewer instructions on every workload. -O4 is not used: its flatten pass does not
# take try_table and aborts. --converge adds nothing. The pass is deterministic, so its output is
# as reproducible as the link's. It drops the name section, since no -g is given.
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
WASM_OPT="$BUILD/tools/$BINARYEN_NAME/bin/wasm-opt"
# The pass, and the features perl.wasm uses as wasm-opt names them, written out so a review sees
# them. Before the pass, wasm-opt's own reading of the module must give exactly this list.
PASS='-O3'
FEATURES='
--enable-mutable-globals
--enable-nontrapping-float-to-int
--enable-bulk-memory
--enable-bulk-memory-opt
--enable-sign-ext
--enable-exception-handling
--enable-reference-types
--enable-multivalue
--enable-extended-const
--enable-call-indirect-overlong
--enable-simd
'
# The stamp: this step's inputs and the library's stamp, then the tool by folder, tarball and
# pin, the pass and its features, so a change to any of them runs the step again.
binaryen_pin=$(pin_of "$BINARYEN_TARBALL")
stamp=$({
    cat "$0" "$(dirname "$0")/lib_index.sh" "$(dirname "$0")/lib.sh" "$BUILD/.library.stamp"
    printf '%s\n' "$BINARYEN_NAME" "$BINARYEN_TARBALL" "$binaryen_pin" $PASS $FEATURES
} | sha256sum | cut -d' ' -f1)
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

# Into the set, through the pass. BINARYEN_CORES caps its threads as WASI_PERL_JOBS caps make's;
# the output is the same bytes for any number.
[ -f "$WASM_OPT" ] || fail "$WASM_OPT is missing; run scripts/00_download.sh"
same_features "$WASM_OPT" "$LINK/perl.wasm" $FEATURES
run "$LOG" env BINARYEN_CORES="$JOBS" "$WASM_OPT" $PASS $FEATURES "$LINK/perl.wasm" -o "$ART/perl.wasm"
[ -f "$ART/perl.wasm" ] || fail "wasm-opt wrote no $ART/perl.wasm (see $LOG)"

# SHA256SUMS, last.
cd "$ART"
listed . ! -path ./SHA256SUMS >"$BUILD/SHA256SUMS.tmp"
mv "$BUILD/SHA256SUMS.tmp" SHA256SUMS
echo "$stamp" >"$STAMP"
echo "40_link: ok ($ART/perl.wasm, $(wc -l <SHA256SUMS | tr -d ' ') files in $ART/SHA256SUMS)"
