#!/bin/sh
# Lay out the library the runner grants read-only at /usr/local/lib/perl5, and the lists that
# say what the artifact set holds.
#
# Perl's own installperl runs on the host miniperl of the target tree, so it installs exactly
# what the target's Config says, into a staging folder. The installed privlib (archlib lies
# inside it) is copied to build/artifacts/lib/perl5/5.42 without what a sandboxed run never
# reads: pod/ and *.pod, the CORE/ headers, static archives, .packlist and *.bs files.
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
stamp=$(stamp_of "$0" "$BUILD/.target.stamp")
if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$stamp" ] && [ -f "$ART/SHA256SUMS" ]; then
    echo "30_library: up to date"
    exit 0
fi

: >"$LOG"
rm -rf "$STAGE" "$ART/lib" "$ART/dev" "$ART/MANIFEST" "$ART/SHA256SUMS" "$STAMP"
cd "$SRC"
run "$LOG" ./miniperl installperl --destdir="$STAGE"
[ -d "$STAGE/$PRIVLIB" ] || fail "installperl did not install $PRIVLIB (see $LOG)"

mkdir -p "$ART/lib/perl5" "$ART/dev"
cp -R "$STAGE/$PRIVLIB" "$ART/lib/perl5/5.42"
lib="$ART/lib/perl5/5.42"
find "$lib" \( -name pod -o -name CORE \) -type d -prune -exec rm -rf {} +
find "$lib" -type f \( -name '*.pod' -o -name '*.a' -o -name '.packlist' -o -name '*.bs' \) -delete
find "$lib" -depth -type d -empty -delete
# %Config names the build's tools by the checkout's absolute path. The guest cannot run them,
# and the path would make the library's bytes depend on where it was built, so it is replaced.
for f in "$lib/wasm32-wasi/Config.pm" "$lib/wasm32-wasi/Config_heavy.pl"; do
    sed "s|$ROOT|/wasi-perl-build|g" "$f" >"$f.tmp" && mv "$f.tmp" "$f"
done
if grep -rlF "$ROOT" "$lib" >/dev/null; then
    fail "a library file still names $ROOT"
fi
: >"$ART/dev/null"

cd "$ART"
# sha256sum marks a name holding a backslash or a newline; the lists' readers expect plain names.
if find . | grep -q '[\\]'; then
    fail "an artifact file name holds a backslash"
fi
# listed <find arguments>: "<sha256>  <path>" per file, sorted by path component (as Python
# sorts paths: "a/x" before "a-b/x"), so the runner's own writer gives the same bytes.
listed() {
    find "$@" -type f | sed 's|^\./||' | sed 's|/|\x01|g' | LC_ALL=C sort | sed 's|\x01|/|g' \
        | xargs sha256sum --
}
listed lib dev >"$BUILD/MANIFEST.tmp"
mv "$BUILD/MANIFEST.tmp" MANIFEST
# SHA256SUMS lists every file of the set but itself: perl.wasm, MANIFEST, lib/ and dev/.
listed . ! -path ./SHA256SUMS >"$BUILD/SHA256SUMS.tmp"
mv "$BUILD/SHA256SUMS.tmp" SHA256SUMS
echo "$stamp" >"$STAMP"
echo "30_library: ok ($(wc -l <MANIFEST | tr -d ' ') files in $ART/MANIFEST)"
