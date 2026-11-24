#!/bin/sh
# Cross-build perl for wasm32-wasip1 from the pinned tarball.
# The target is described by the hand-written recipes/config.sh; Configure -S only substitutes
# it into the .SH files and probes nothing. make then runs with the host tools from build/native.
. "$(dirname "$0")/lib.sh"
LOG="$LOGS/20_target.log"
TARBALL="$DOWNLOADS/perl-$PERL_VERSION.tar.xz"
SRC="$BUILD/target"
NATIVE="$BUILD/native"
SDK="$BUILD/tools/$WASI_SDK_NAME"
SYSROOT="$SDK/share/wasi-sysroot"
STUBS="$BUILD/stubs"
ART="$BUILD/artifacts"
DESTDIR="$BUILD/target-install"
STAMP="$BUILD/.target.stamp"
PATCHES="$ROOT/recipes/patches/perl-$PERL_VERSION"

[ -f "$TARBALL" ] || fail "$TARBALL is missing; run scripts/00_download.sh"
[ -x "$SDK/bin/clang" ] || fail "wasi-sdk is not unpacked; run scripts/00_download.sh"
[ -x "$NATIVE/miniperl" ] && [ -x "$NATIVE/generate_uudmap" ] || fail "host tools are missing; run scripts/10_native.sh"
want=$(grep " perl-$PERL_VERSION.tar.xz " "$DOWNLOADS/SHA256SUMS" | cut -d' ' -f1)
echo "$want  $TARBALL" | sha256sum -c --status || fail "$TARBALL does not match downloads/SHA256SUMS"

inputs=$(find "$ROOT/recipes" "$0" "$(dirname "$0")/lib.sh" -type f | LC_ALL=C sort)
# shellcheck disable=SC2086
stamp=$(echo "$want" | stamp_of - $inputs "$BUILD/.native.stamp")
if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$stamp" ] && [ -f "$ART/perl.wasm" ]; then
    echo "20_target: up to date"
    exit 0
fi

: >"$LOG"
rm -rf "$SRC" "$DESTDIR" "$ART" "$STUBS" "$BUILD/host-tools" "$STAMP"

# The stand-ins for what wasi-libc lacks, linked into every perl link through $libs.
mkdir -p "$STUBS"
run "$LOG" "$ROOT/recipes/wasi-cc" -O2 -Wall -Wextra -Werror -c "$ROOT/recipes/wasi-stubs.c" -o "$STUBS/wasi-stubs.o"

mkdir -p "$BUILD/unpack-target"
run "$LOG" tar -C "$BUILD/unpack-target" -xf "$TARBALL"
mv "$BUILD/unpack-target/perl-$PERL_VERSION" "$SRC"
rmdir "$BUILD/unpack-target"
cd "$SRC"
if [ -d "$PATCHES" ]; then
    for p in "$PATCHES"/*.patch; do
        [ -f "$p" ] || continue
        run "$LOG" patch -p1 -i "$p"
    done
fi

sed -e "s|@ROOT@|$ROOT|g" -e "s|@BUILD@|$BUILD|g" -e "s|@SDK@|$SDK|g" -e "s|@SYSROOT@|$SYSROOT|g" \
    -e "s|@NATIVE@|$NATIVE|g" -e "s|@NATIVEPERL@|$BUILD/native-install/bin/perl|g" \
    "$ROOT/recipes/config.sh" >config.sh
run "$LOG" sh ./Configure -S
# make links generate_uudmap to the host's copy and rebuilds the link whenever the copy is
# older than generate_uudmap.o; under -j two makes then race to recreate it. A fresh copy,
# made after that object, keeps the link up to date.
run "$LOG" make generate_uudmap.o
mkdir -p "$BUILD/host-tools"
cp "$NATIVE/generate_uudmap" "$BUILD/host-tools/generate_uudmap"
# The utilities are extracted by running perl on their .PL files; the guest perl cannot run on
# the build host, so the host miniperl extracts them first and make then finds them up to date.
run "$LOG" make -j"$(nproc)" perl utils/Makefile
run "$LOG" make -C utils PERL=../miniperl REALPERL=../miniperl
run "$LOG" make -j"$(nproc)"
[ -f perl ] || fail "make did not produce perl (see $LOG)"
# installperl runs on the host miniperl. It needs Cwd, an XS module this build does not compile
# for the guest, so Cwd's pure-Perl fallback from the source tree is loaded before installperl
# narrows @INC to lib/.
run "$LOG" ./miniperl -Ilib -MCwd -MFile::Spec installperl --destdir="$DESTDIR"

mkdir -p "$ART/lib/perl5" "$ART/dev"
cp perl "$ART/perl.wasm"
cp -R "$DESTDIR/usr/local/lib/perl5/5.42" "$ART/lib/perl5/5.42"
: >"$ART/dev/null"
echo "$stamp" >"$STAMP"
echo "20_target: ok ($ART/perl.wasm)"
