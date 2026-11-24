#!/bin/sh
# Cross-build perl for wasm32-wasip1 from the pinned tarball.
# The target is described by the hand-written recipes/config.sh; Configure -S only substitutes
# it into the .SH files and probes nothing. make then runs with the host tools from build/native.
# The XS distributions of recipes/cpan-xs join the tree under cpan/, so make_ext.pl builds and
# links them as it does the core's own static extensions.
. "$(dirname "$0")/lib.sh"
LOG="$LOGS/20_target.log"
TARBALL="$DOWNLOADS/perl-$PERL_VERSION.tar.xz"
SRC="$BUILD/target"
NATIVE="$BUILD/native"
SDK="$BUILD/tools/$WASI_SDK_NAME"
SYSROOT="$SDK/share/wasi-sysroot"
STUBS="$BUILD/stubs"
ART="$BUILD/artifacts"
STAMP="$BUILD/.target.stamp"
PATCHES="$ROOT/recipes/patches/perl-$PERL_VERSION"

[ -f "$TARBALL" ] || fail "$TARBALL is missing; run scripts/00_download.sh"
[ -x "$SDK/bin/clang" ] || fail "wasi-sdk is not unpacked; run scripts/00_download.sh"
[ -x "$NATIVE/miniperl" ] && [ -x "$NATIVE/generate_uudmap" ] || fail "host tools are missing; run scripts/10_native.sh"
want=$(grep " perl-$PERL_VERSION.tar.xz " "$DOWNLOADS/SHA256SUMS" | cut -d' ' -f1)
echo "$want  $TARBALL" | sha256sum -c --status || fail "$TARBALL does not match downloads/SHA256SUMS"

# Each listed XS tarball is checked against its pin, and its module named in config.sh's three
# lists, before anything is built or removed. The stamp holds each pin, not only the list.
xs=$(awk '!/^#/ && NF { print $1 "=" $2 }' "$ROOT/recipes/cpan-xs")
pins=""
for entry in $xs; do
    t=${entry%%=*} folder=${entry#*=}
    [ -n "$folder" ] && [ "$folder" = "$(basename "$folder")" ] \
        || fail "recipes/cpan-xs gives $t no folder under cpan/"
    check_pinned "$t"
    pins="$pins $t=$(pin_of "$t")"
    module=$(printf '%s\n' "$folder" | tr - /)
    for var in static_ext extensions known_extensions; do
        sed -n "s/^$var='\(.*\)'\$/ \1 /p" "$ROOT/recipes/config.sh" | grep -qF " $module " \
            || fail "recipes/config.sh's $var does not name $module, which recipes/cpan-xs lists"
    done
done

inputs=$(find "$ROOT/recipes" "$0" "$(dirname "$0")/lib.sh" "$ROOT/tests/build/memchr-test.c" -type f | LC_ALL=C sort)
# shellcheck disable=SC2086
stamp=$(echo "$want$pins" | stamp_of - $inputs "$BUILD/.native.stamp")
if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$stamp" ] && [ -f "$ART/perl.wasm" ]; then
    echo "20_target: up to date"
    exit 0
fi

: >"$LOG"
rm -rf "$SRC" "$ART" "$STUBS" "$BUILD/host-tools" "$BUILD/tests" "$STAMP"

# The stand-ins for what wasi-libc lacks, linked into every perl link through $libs.
mkdir -p "$STUBS"
run "$LOG" "$ROOT/recipes/wasi-cc" -O2 -Wall -Wextra -Werror -c "$ROOT/recipes/wasi-stubs.c" -o "$STUBS/wasi-stubs.o"
run "$LOG" "$ROOT/recipes/wasi-cc" -O2 -Wall -Wextra -Werror -c "$ROOT/recipes/wasi-posix-stubs.c" -o "$STUBS/wasi-posix-stubs.o"
# memchr compares sixteen bytes a step with SIMD128. It alone is compiled for SIMD: building
# everything that way gained nothing measurable.
run "$LOG" "$ROOT/recipes/wasi-cc" -O2 -msimd128 -Wall -Wextra -Werror -c "$ROOT/recipes/wasi-memchr.c" -o "$STUBS/wasi-memchr.o"
# The descriptor table is linked into every program wasi-cc links, so it comes first.
run "$LOG" "$ROOT/recipes/wasi-cc" -O2 -Wall -Wextra -Werror -c "$ROOT/recipes/wasi-fd-table.c" -o "$STUBS/wasi-fd-table.o"
run "$LOG" "$SDK/bin/llvm-ar" rcs "$STUBS/libwasi-perl-fd-table.a" "$STUBS/wasi-fd-table.o"
# The check of memchr against a byte loop, linked with the object perl links; tests/build runs
# it.
mkdir -p "$BUILD/tests"
run "$LOG" "$ROOT/recipes/wasi-cc" -O2 -Wall -Wextra -Werror "$ROOT/tests/build/memchr-test.c" "$STUBS/wasi-memchr.o" -o "$BUILD/tests/memchr-test.wasm"

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
for entry in $xs; do
    unpack_pinned "${entry%%=*}" "cpan/${entry#*=}" "$LOG"
done

sed -e "s|@ROOT@|$ROOT|g" -e "s|@BUILD@|$BUILD|g" -e "s|@SDK@|$SDK|g" -e "s|@SYSROOT@|$SYSROOT|g" \
    -e "s|@NATIVE@|$NATIVE|g" -e "s|@NATIVEPERL@|$BUILD/native-install/bin/perl|g" \
    "$ROOT/recipes/config.sh" >config.sh
run "$LOG" sh ./Configure -S
# The sandbox's refusals stand in for Perl functions, so they are compiled against the configured
# tree with the command Perl's own objects get (Perl's headers trip -Wc++-keyword, so that one
# warning is not an error). wasi-cc links the archive and wraps the symbols.
# shellcheck disable=SC2046
run "$LOG" $(sh cflags "optimize='-O2'" sandbox_refusals.o) -Werror -Wno-c++-keyword -I"$SRC" \
    "$ROOT/recipes/sandbox-refusals.c" -o "$STUBS/sandbox-refusals.o"
run "$LOG" "$SDK/bin/llvm-ar" rcs "$STUBS/libwasi-perl-refusals.a" "$STUBS/sandbox-refusals.o"
# make links generate_uudmap to the host's copy and rebuilds the link whenever the copy is
# older than generate_uudmap.o; under -j two makes then race to recreate it. A fresh copy,
# made after that object, keeps the link up to date.
run "$LOG" make generate_uudmap.o
mkdir -p "$BUILD/host-tools"
cp "$NATIVE/generate_uudmap" "$BUILD/host-tools/generate_uudmap"
# Perl's Makefile orders only its own extensions that need the Unicode tables after mktables,
# which makes them. A CPAN extension's Makefile.PL may need them too (Text-CSV_XS's loads
# charnames, which reads unicore/Name.pm), and under -j it would race mktables. So the tables are
# made first, by a make of their own; a later make finds them up to date and leaves them alone.
run "$LOG" make -j"$JOBS" uni.data
# The utilities are extracted by running perl on their .PL files; the guest perl cannot run on
# the build host, so the host miniperl extracts them first and make then finds them up to date.
run "$LOG" make -j"$JOBS" perl utils/Makefile
run "$LOG" make -C utils PERL=../miniperl REALPERL=../miniperl
run "$LOG" make -j"$JOBS"
[ -f perl ] || fail "make did not produce perl (see $LOG)"
mkdir -p "$ART"
cp perl "$ART/perl.wasm"
echo "$stamp" >"$STAMP"
echo "20_target: ok ($ART/perl.wasm)"
