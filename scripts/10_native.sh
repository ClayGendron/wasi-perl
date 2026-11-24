#!/bin/sh
# Build native perl from the pinned tarball: the host tools the cross build runs (miniperl,
# generate_uudmap) and the oracle native perl that guest output is compared against.
# Runs standalone and is idempotent: a finished build whose tarball is unchanged is skipped.
. "$(dirname "$0")/lib.sh"
LOG="$LOGS/10_native.log"
TARBALL="$DOWNLOADS/perl-$PERL_VERSION.tar.xz"
SRC="$BUILD/native"
PREFIX="$BUILD/native-install"
STAMP="$BUILD/.native.stamp"

[ -f "$TARBALL" ] || fail "$TARBALL is missing; run scripts/00_download.sh"
want=$(grep " perl-$PERL_VERSION.tar.xz " "$DOWNLOADS/SHA256SUMS" | cut -d' ' -f1)
echo "$want  $TARBALL" | sha256sum -c --status || fail "$TARBALL does not match downloads/SHA256SUMS"

stamp=$(echo "$want $0" | stamp_of - "$0")
if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$stamp" ] \
    && [ -x "$PREFIX/bin/perl" ] && [ -x "$SRC/miniperl" ] && [ -x "$SRC/generate_uudmap" ]; then
    echo "10_native: up to date"
    exit 0
fi

: >"$LOG"
rm -rf "$SRC" "$PREFIX" "$STAMP"
mkdir -p "$BUILD/unpack-native"
run "$LOG" tar -C "$BUILD/unpack-native" -xf "$TARBALL"
mv "$BUILD/unpack-native/perl-$PERL_VERSION" "$SRC"
rmdir "$BUILD/unpack-native"
cd "$SRC"
run "$LOG" sh ./Configure -des -Dprefix="$PREFIX" -Dman1dir=none -Dman3dir=none
run "$LOG" make -j"$JOBS"
run "$LOG" make install
[ -x "$PREFIX/bin/perl" ] || fail "native perl did not install to $PREFIX (see $LOG)"
echo "$stamp" >"$STAMP"
echo "10_native: ok ($PREFIX/bin/perl)"
