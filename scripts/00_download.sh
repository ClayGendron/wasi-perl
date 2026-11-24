#!/bin/sh
# Fetch every pinned input into downloads/ and verify it against downloads/SHA256SUMS.
# WASI_PERL_DOWNLOAD_CACHE, if set, names a folder to copy a file from instead of fetching it;
# the copy is verified exactly as a download is.
. "$(dirname "$0")/lib.sh"
LOG="$LOGS/00_download.log"
: >"$LOG"

while read -r sum name url; do
    [ -n "$sum" ] || continue
    dest="$DOWNLOADS/$name"
    if [ -f "$dest" ] && echo "$sum  $dest" | sha256sum -c --status; then
        echo "verified $name" >>"$LOG"
        continue
    fi
    rm -f "$dest" "$dest.part"
    if [ -n "${WASI_PERL_DOWNLOAD_CACHE:-}" ] && [ -f "$WASI_PERL_DOWNLOAD_CACHE/$name" ]; then
        run "$LOG" cp "$WASI_PERL_DOWNLOAD_CACHE/$name" "$dest.part"
    else
        n=0
        until curl -fsSL --retry 4 -o "$dest.part" "$url" >>"$LOG" 2>&1; do
            n=$((n + 1))
            [ "$n" -lt 3 ] || fail "could not fetch $url (see $LOG)"
            sleep 2
        done
    fi
    if ! echo "$sum  $dest.part" | sha256sum -c --status; then
        rm -f "$dest.part"
        fail "$name does not match its SHA-256 in downloads/SHA256SUMS"
    fi
    mv "$dest.part" "$dest"
    echo "verified $name" >>"$LOG"
done <"$DOWNLOADS/SHA256SUMS"

# Unpack the tools under build/ (the Perl tarball is unpacked fresh by each step that needs it).
mkdir -p "$BUILD/tools"
for t in "$WASI_SDK_NAME:tar.gz" "$WASMTIME_NAME:tar.xz"; do
    name=${t%%:*}; ext=${t#*:}
    if [ ! -f "$BUILD/tools/$name/.unpacked" ]; then
        rm -rf "$BUILD/tools/$name"
        run "$LOG" tar -C "$BUILD/tools" -xf "$DOWNLOADS/$name.$ext"
        [ -d "$BUILD/tools/$name" ] || fail "$name.$ext did not unpack to $name/"
        touch "$BUILD/tools/$name/.unpacked"
    fi
done
# binaryen's tarball is named apart from its folder, so it is unpacked on its own. It runs only
# as a build tool: 40_link.sh's wasm-opt.
if [ ! -f "$BUILD/tools/$BINARYEN_NAME/.unpacked" ]; then
    rm -rf "$BUILD/tools/$BINARYEN_NAME"
    run "$LOG" tar -C "$BUILD/tools" -xf "$DOWNLOADS/$BINARYEN_TARBALL"
    [ -f "$BUILD/tools/$BINARYEN_NAME/bin/wasm-opt" ] \
        || fail "$BINARYEN_TARBALL did not unpack to $BINARYEN_NAME/bin/wasm-opt"
    touch "$BUILD/tools/$BINARYEN_NAME/.unpacked"
fi
echo "00_download: ok"
