#!/bin/sh
# Build native perl from the pinned tarball: the host tools the cross build runs (miniperl,
# generate_uudmap) and the oracle native perl that guest output is compared against.
# Then the CPAN distributions of recipes/cpan-xs and recipes/cpan-pure, built for that perl from
# the same pinned tarballs as the guest's, into build/native-cpan: a folder of their own, so the
# oracle sees no module the core lacks, and a check can put them on PERL5LIB to compare the
# guest's modules with native ones of the same versions.
# Runs standalone and is idempotent: a finished build whose tarballs are unchanged is skipped.
. "$(dirname "$0")/lib.sh"
LOG="$LOGS/10_native.log"
TARBALL="$DOWNLOADS/perl-$PERL_VERSION.tar.xz"
SRC="$BUILD/native"
PREFIX="$BUILD/native-install"
STAMP="$BUILD/.native.stamp"
CPAN_PREFIX="$BUILD/native-cpan"
CPAN_SRC="$BUILD/native-cpan-src"

[ -f "$TARBALL" ] || fail "$TARBALL is missing; run scripts/00_download.sh"
want=$(grep " perl-$PERL_VERSION.tar.xz " "$DOWNLOADS/SHA256SUMS" | cut -d' ' -f1)
echo "$want  $TARBALL" | sha256sum -c --status || fail "$TARBALL does not match downloads/SHA256SUMS"

# Every listed tarball is checked against its pin before anything is built or removed.
cpan=$(listed_tarballs "$ROOT/recipes/cpan-xs"; listed_tarballs "$ROOT/recipes/cpan-pure")
pins=""
for t in $cpan; do
    check_pinned "$t"
    pins="$pins $t=$(pin_of "$t")"
done

stamp=$(echo "$want $0$pins" | stamp_of - "$0" "$(dirname "$0")/lib.sh")
if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$stamp" ] \
    && [ -x "$PREFIX/bin/perl" ] && [ -x "$SRC/miniperl" ] && [ -x "$SRC/generate_uudmap" ] \
    && [ -d "$CPAN_PREFIX/lib/perl5" ]; then
    echo "10_native: up to date"
    exit 0
fi

: >"$LOG"
rm -rf "$SRC" "$PREFIX" "$CPAN_PREFIX" "$CPAN_SRC" "$STAMP"
mkdir -p "$BUILD/unpack-native"
run "$LOG" tar -C "$BUILD/unpack-native" -xf "$TARBALL"
mv "$BUILD/unpack-native/perl-$PERL_VERSION" "$SRC"
rmdir "$BUILD/unpack-native"
cd "$SRC"
run "$LOG" sh ./Configure -des -Dprefix="$PREFIX" -Dman1dir=none -Dman3dir=none
run "$LOG" make -j"$JOBS"
run "$LOG" make install
[ -x "$PREFIX/bin/perl" ] || fail "native perl did not install to $PREFIX (see $LOG)"

# The CPAN distributions, XS first, so that each later one finds those it uses. The environment
# names no other install place, and the default answer is given to any question Makefile.PL asks.
mkdir -p "$CPAN_SRC"
for t in $cpan; do
    dist="$CPAN_SRC/$(dist_of "$t")"
    unpack_pinned "$t" "$dist" "$LOG"
    cd "$dist"
    run "$LOG" env -u PERL_MM_OPT -u PERL_MB_OPT -u PERL_LOCAL_LIB_ROOT \
        PERL5LIB="$CPAN_PREFIX/lib/perl5" PERL_MM_USE_DEFAULT=1 \
        "$PREFIX/bin/perl" Makefile.PL INSTALL_BASE="$CPAN_PREFIX" </dev/null
    run "$LOG" make -j"$JOBS"
    run "$LOG" make install
done
[ -d "$CPAN_PREFIX/lib/perl5" ] || fail "the CPAN distributions did not install to $CPAN_PREFIX (see $LOG)"
echo "$stamp" >"$STAMP"
echo "10_native: ok ($PREFIX/bin/perl)"
