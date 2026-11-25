#!/bin/sh
# Research prototype, not shipped: links perl-reactor.wasm from the objects of a finished build
# the way spec 022's Decision 2 says step 4 of 40_link.sh would, then runs 021's wasm-opt pass
# over it. Run from the worktree root, under the machine lock:
#   flock /home/user/wasi-perl-wt/machine.lock context/research/2026-11-24-preload-image-prototype/build.sh
# Writes, in build/proto022/:
#   wasi-reactor.o, fdtable-reset.o   the reactor's objects
#   perl-command.wasm                 a control: the command relinked by the same make call with
#                                     perlmain.o, which must equal build/link/perl.wasm
#   perl-reactor.wasm                 wasm-ld's output
#   perl-reactor.opt.wasm             after wasm-opt -O3 with 021's features
# build/target/perl is moved aside for each link and put back, so the tree's perl stays the
# command.
set -eu
ROOT=$(pwd)
E="$ROOT/context/research/2026-11-24-preload-image-prototype"
T="$ROOT/build/target"
OUT="$ROOT/build/proto022"
LOG="$ROOT/build/logs/proto022-build.log"
WASM_OPT="$ROOT/build/tools/binaryen-version_133/bin/wasm-opt"
JOBS=${WASI_PERL_JOBS:-4}
mkdir -p "$OUT"
: >"$LOG"

# This build tree was made in another worktree and moved here, so its Makefile and cflags name
# that worktree's compiler and stubs. Each is named again for this one.
OLD=$(sed -n 's|^CC = \(.*\)/recipes/wasi-cc$|\1|p' "$T/Makefile")
CC="$ROOT/recipes/wasi-cc"
cc=$(cd "$T" && sh cflags "optimize='-O2'" perlmain.o | sed "s|^[^ ]*wasi-cc|$CC|")
# perlmain.c's xs_init names its own file (__FILE__) in every newXS. Perl's make compiles it as
# "perlmain.c" from the tree; included from here it would be found by its absolute path, and the
# build host's folders would be in the reactor's data. The prefix map keeps the relative name.
# shellcheck disable=SC2086
$cc -I"$T" -fmacro-prefix-map="$T/=" -fmacro-prefix-map="$E/=" "$E/wasi-reactor.c" \
    -o "$OUT/wasi-reactor.o" >>"$LOG" 2>&1
"$CC" -O2 -Wall -Wextra -Werror -c "$E/fdtable-reset.c" -o "$OUT/fdtable-reset.o" >>"$LOG" 2>&1

libs=$(sed -n "s/^libs='\(.*\)'\$/\1/p" "$T/config.sh" | sed "s|$OLD|$ROOT|g")
cldflags=$(sed -n 's/^CLDFLAGS = //p' "$T/Makefile")
wraps="-Wl,--wrap=Perl_newSVpvn_share,--wrap=Perl_boot_core_PerlIO,--wrap=Perl_boot_core_UNIVERSAL"
wraps="$wraps,--wrap=Perl_boot_core_builtin,--wrap=Perl_boot_core_mro,--wrap=Perl_newXS"
wraps="$wraps,--wrap=__wasi_clock_time_get"
# The moved tree's dependency lines name headers under the old worktree, so make would try to
# rebuild every object; each prerequisite of the perl rule is held old instead. In a build made
# in place, none of this is needed: everything but the link is up to date.
olds=""
for f in libperl.a ext.libs write_buildcustomize.pl perlmain.o \
    $(sed -n 's/^static_ext = *//p' "$T/Makefile" | sed 's/\$(LIB_EXT)/.a/g'); do
    olds="$olds -o $f"
done

relink() { # relink <out> <perlmain_objs> <CLDFLAGS>
    cd "$T"
    mv perl perl.command
    trap 'cd "$T" && mv -f perl.command perl' EXIT
    # shellcheck disable=SC2086
    make perl $olds "CC=$CC" "perlmain_objs=$2" "libs=$ROOT/build/index/lib-index.o $libs" \
        "CLDFLAGS=$3" >>"$LOG" 2>&1
    cp perl "$1"
    mv -f perl.command perl
    trap - EXIT
    cd "$ROOT"
}
relink "$OUT/perl-command.wasm" perlmain.o "$cldflags"
relink "$OUT/perl-reactor.wasm" "$OUT/wasi-reactor.o $OUT/fdtable-reset.o" \
    "$cldflags -mexec-model=reactor $wraps"

FEATURES="--enable-mutable-globals --enable-nontrapping-float-to-int --enable-bulk-memory
--enable-bulk-memory-opt --enable-sign-ext --enable-exception-handling --enable-reference-types
--enable-multivalue --enable-extended-const --enable-call-indirect-overlong --enable-simd"
have=$("$WASM_OPT" --print-features "$OUT/perl-reactor.wasm" | LC_ALL=C sort)
want=$(printf '%s\n' $FEATURES | LC_ALL=C sort)
[ "$have" = "$want" ] || { echo "features differ: $have" >&2; exit 1; }
# shellcheck disable=SC2086
env BINARYEN_CORES="$JOBS" "$WASM_OPT" -O3 $FEATURES "$OUT/perl-reactor.wasm" \
    -o "$OUT/perl-reactor.opt.wasm" >>"$LOG" 2>&1
sha256sum "$ROOT/build/link/perl.wasm" "$OUT"/*.wasm
wc -c "$OUT"/*.wasm
