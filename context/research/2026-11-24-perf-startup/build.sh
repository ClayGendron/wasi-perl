#!/bin/sh
# Builds the research reactor (perl-reactor.c) and its wizer snapshots from a finished build.
# Run from the worktree root, under the machine lock:
#   flock /home/user/wasi-perl-wt/machine.lock context/research/2026-11-24-perf-startup/build.sh
# Writes, in build/research/:
#   reactor.wasm          perl.wasm as a reactor, not initialised
#   snap-construct.wasm   wizer snapshot after perl_construct
#   reactor-pre.wasm      the reactor with PRELOAD compiled in
#   snap-preload.wasm     wizer snapshot with strict, warnings, POSIX, List::Util, JSON::PP,
#                         Data::Dumper, Storable and Encode loaded
set -eu
ROOT=$(pwd)
E="$ROOT/context/research/2026-11-24-perf-startup"
T="$ROOT/build/target"
R="$ROOT/build/research"
W=/home/user/wasi-perl-downloads/wasmtime-v51.0.0-x86_64-linux/wasmtime
mkdir -p "$R"
# Perl's own compile command for perlmain.o, with this worktree's compiler wrapper.
cc=$(cd "$T" && sh cflags "optimize='-O2'" perlmain.o | sed "s|^[^ ]*wasi-cc|$ROOT/recipes/wasi-cc|")
# shellcheck disable=SC2086
$cc -Wno-c++-compat -Wno-write-strings -I"$T" "$E/perl-reactor.c" -o "$R/perl-reactor.o"
# shellcheck disable=SC2086
$cc -Wno-c++-compat -Wno-write-strings -I"$T" -DPRELOAD='"-mstrict", "-mwarnings", "-mPOSIX", "-mList::Util", "-mJSON::PP", "-mData::Dumper", "-mStorable", "-mEncode"' \
    "$E/perl-reactor.c" -o "$R/perl-reactor-pre.o"
"$ROOT/recipes/wasi-cc" -O2 -c "$E/fdtable-reset.c" -o "$R/fdtable-reset.o"
for v in reactor reactor-pre; do
    obj="$R/perl-$v.o"
    sh "$E/link.sh" "$v" "$obj" "$R/fdtable-reset.o" -mexec-model=reactor -Wl,--wrap=Perl_newSVpvn_share,--wrap=Perl_boot_core_PerlIO,--wrap=Perl_boot_core_UNIVERSAL,--wrap=Perl_boot_core_builtin,--wrap=Perl_boot_core_mro,--wrap=Perl_newXS
done
FLAGS="-W exceptions=y -S cli=y,inherit-stdin=n,inherit-stdout=n,inherit-stderr=n -C cache=n --init-func wizer.initialize"
LIB="--dir $ROOT/build/artifacts/lib/perl5::/usr/local/lib/perl5 --dir $ROOT/build/artifacts/dev::/dev"
# shellcheck disable=SC2086
"$W" wizer $FLAGS $LIB -o "$R/snap-construct.wasm" "$R/reactor.wasm"
# shellcheck disable=SC2086
"$W" wizer $FLAGS $LIB -o "$R/snap-preload.wasm" "$R/reactor-pre.wasm"
ls -l "$R"/reactor*.wasm "$R"/snap-*.wasm
