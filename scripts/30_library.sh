#!/bin/sh
# Lay out the library the runner grants read-only at /usr/local/lib/perl5, and the lists that
# say what the artifact set holds.
#
# Perl's own installperl runs on the host miniperl of the target tree, so it installs exactly
# what the target's Config says, into a staging folder. The installed privlib (archlib lies
# inside it) is copied to build/artifacts/lib/perl5/5.42 without what a sandboxed run never
# reads: pod/ and *.pod, the CORE/ headers, static archives with the extralibs.ld lists of
# what to link them with, .packlist and *.bs files, and what only builds XS code (C headers,
# Encode's enc2xs templates, xsubpp and its typemap). The pure-Perl distributions of
# recipes/cpan-pure are laid in first, each lib/ tree as the distribution ships it, so the same
# rules cover them; one that would replace a file already there is refused.
#
# build/artifacts/MANIFEST is "<sha256>  <path>" for every file under lib/ and dev/, paths
# relative to build/artifacts; build/artifacts/SHA256SUMS is the same for every file of the set
# but itself (perl.wasm, MANIFEST, lib/ and dev/), the list the runner checks a set against.
. "$(dirname "$0")/lib.sh"
LOG="$LOGS/30_library.log"
SRC="$BUILD/target"
STAGE="$BUILD/stage"
ART="$BUILD/artifacts"
STAMP="$BUILD/.library.stamp"
PRIVLIB=usr/local/lib/perl5/5.42

[ -f "$BUILD/.target.stamp" ] && [ -f "$ART/perl.wasm" ] || fail "perl.wasm is missing; run scripts/20_target.sh"
# Each listed tarball is checked against its pin before anything is removed; the stamp holds
# each pin, not only the list.
pure=$(listed_tarballs "$ROOT/recipes/cpan-pure")
pins=""
for t in $pure; do
    check_pinned "$t"
    pins="$pins $t=$(pin_of "$t")"
done
stamp=$(echo "$pins" | stamp_of - "$0" "$(dirname "$0")/lib.sh" "$BUILD/.target.stamp")
if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$stamp" ] && [ -f "$ART/SHA256SUMS" ]; then
    echo "30_library: up to date"
    exit 0
fi

: >"$LOG"
rm -rf "$STAGE" "$BUILD/cpan-pure" "$ART/lib" "$ART/dev" "$ART/MANIFEST" "$ART/SHA256SUMS" "$STAMP"
cd "$SRC"
run "$LOG" ./miniperl installperl --destdir="$STAGE"
[ -d "$STAGE/$PRIVLIB" ] || fail "installperl did not install $PRIVLIB (see $LOG)"

mkdir -p "$ART/lib/perl5" "$ART/dev"
cp -R "$STAGE/$PRIVLIB" "$ART/lib/perl5/5.42"
lib="$ART/lib/perl5/5.42"
mkdir -p "$BUILD/cpan-pure"
for t in $pure; do
    dist="$BUILD/cpan-pure/$(dist_of "$t")"
    unpack_pinned "$t" "$dist" "$LOG"
    [ -d "$dist/lib" ] || fail "$t has no lib/ folder"
    cd "$dist/lib"
    [ -z "$(find . ! -type f ! -type d)" ] || fail "$t's lib/ holds something that is neither a file nor a folder"
    find . -type f | sed 's|^\./||' | while read -r f; do
        [ ! -e "$lib/$f" ] || fail "$t's lib/$f would replace a file already in the library"
        mkdir -p "$lib/$(dirname "$f")"
        cp "$f" "$lib/$f"
        # As installperl installs a module: read-only.
        chmod 0444 "$lib/$f"
    done
done
cd "$SRC"
find "$lib" \( -name pod -o -name CORE \) -type d -prune -exec rm -rf {} +
find "$lib" -type f \( -name '*.pod' -o -name '*.a' -o -name '.packlist' -o -name '*.bs' \
    -o -name 'extralibs.ld' -o -name 'extralibs.all' -o -name '*.h' -o -name '*.e2x' \) -delete
# xsubpp and its typemap turn XS into C for a compiler; the sandbox has none.
rm -f "$lib/ExtUtils/xsubpp" "$lib/ExtUtils/typemap"
find "$lib" -depth -type d -empty -delete
# %Config names the build's tools by the checkout's absolute path. The guest cannot run them,
# and the path would make the library's bytes depend on where it was built, so it is replaced.
root_re=$(printf '%s' "$ROOT" | sed 's/[][\\.*^$|&/]/\\&/g')
for f in "$lib/wasm32-wasi/Config.pm" "$lib/wasm32-wasi/Config_heavy.pl"; do
    sed "s|$root_re|/wasi-perl-build|g" "$f" >"$f.tmp" && mv "$f.tmp" "$f"
done
if grep -rlF "$ROOT" "$lib" >/dev/null; then
    fail "a library file still names $ROOT"
fi
: >"$ART/dev/null"

cd "$ART"
# sha256sum marks a name holding a backslash or a newline, and the lists' readers split on
# blanks; a name with either, or with a quote, is refused rather than listed wrongly.
if find . | grep -q "[[:space:]\\\\'\"]"; then
    fail "an artifact file name holds a blank, a quote or a backslash"
fi
# listed <find arguments>: "<sha256>  <path>" per file, sorted by path component (as Python
# sorts paths: "a/x" before "a-b/x"), so the runner's own writer gives the same bytes.
listed() {
    find "$@" -type f | sed 's|^\./||' | sed 's|/|\x01|g' | LC_ALL=C sort | sed 's|\x01|/|g' \
        | xargs -d '\n' sha256sum --
}
listed lib dev >"$BUILD/MANIFEST.tmp"
mv "$BUILD/MANIFEST.tmp" MANIFEST
# SHA256SUMS lists every file of the set but itself: perl.wasm, MANIFEST, lib/ and dev/.
listed . ! -path ./SHA256SUMS >"$BUILD/SHA256SUMS.tmp"
mv "$BUILD/SHA256SUMS.tmp" SHA256SUMS
echo "$stamp" >"$STAMP"
echo "30_library: ok ($(wc -l <MANIFEST | tr -d ' ') files in $ART/MANIFEST)"
