# Shared helpers for the build steps. Sourced, never run.
# Every step logs to build/logs/<step>.log and reports a failure as one line.

set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
BUILD="$ROOT/build"
DOWNLOADS="$ROOT/downloads"
LOGS="$BUILD/logs"
PERL_VERSION=5.42.3
WASI_SDK_NAME=wasi-sdk-34.0-x86_64-linux
WASMTIME_NAME=wasmtime-v51.0.0-x86_64-linux
# binaryen's tarball is named apart from the one folder it unpacks to.
BINARYEN_TARBALL=binaryen-version_133-x86_64-linux.tar.gz
BINARYEN_NAME=binaryen-version_133
# WASI_PERL_JOBS caps make's parallelism when other builds share the machine.
JOBS=${WASI_PERL_JOBS:-$(nproc)}
mkdir -p "$LOGS"

# run <log> <command...>: runs the command with its output in the log; on failure prints one line.
run() {
    log="$1"; shift
    if ! "$@" >>"$log" 2>&1; then
        echo "FAILED: $* (see $log)" >&2
        exit 1
    fi
}

# fail <message>: one line on stderr, then exit.
fail() {
    echo "FAILED: $*" >&2
    exit 1
}

# stamp_of <files...>: a hash of the given input files, used to skip a step whose inputs are unchanged.
stamp_of() {
    cat "$@" | sha256sum | cut -d' ' -f1
}

# pin_of <name>: the SHA-256 that downloads/SHA256SUMS pins for <name>. A name pinned on no line,
# or on more than one, is refused.
pin_of() {
    _pins=$(awk -v n="$1" '$2 == n { print $1 }' "$DOWNLOADS/SHA256SUMS")
    [ -n "$_pins" ] && [ "$(printf '%s\n' "$_pins" | wc -l)" -eq 1 ] \
        || fail "$1 is not pinned in downloads/SHA256SUMS"
    printf '%s\n' "$_pins"
}

# check_pinned <name>: downloads/<name> is there and matches its pin.
check_pinned() {
    _pin=$(pin_of "$1")
    [ -f "$DOWNLOADS/$1" ] || fail "downloads/$1 is missing; run scripts/00_download.sh"
    echo "$_pin  $DOWNLOADS/$1" | sha256sum -c --status \
        || fail "$1 does not match its SHA-256 in downloads/SHA256SUMS"
}

# same_features <wasm-opt> <module> <flag...>: the features wasm-opt reads in the module are
# exactly the flags given, in any order; else one line names the difference.
same_features() {
    _tool=$1 _module=$2
    shift 2
    _have=$("$_tool" --print-features "$_module" 2>/dev/null) \
        || fail "wasm-opt could not read the features of $_module"
    _have=$(printf '%s\n' $_have | LC_ALL=C sort)
    _want=$(printf '%s\n' "$@" | LC_ALL=C sort)
    [ "$_have" = "$_want" ] && return 0
    _uses="" _names=""
    for _f in $_have; do
        case " $* " in *" $_f "*) ;; *) _uses="$_uses $_f" ;; esac
    done
    for _f in "$@"; do
        case " $(echo $_have) " in *" $_f "*) ;; *) _names="$_names $_f" ;; esac
    done
    _why=""
    [ -z "$_uses" ] || _why="it uses$_uses, which the pass does not name"
    [ -z "$_names" ] || _why="${_why:+$_why; }the pass names$_names, which it does not use"
    fail "the features of $(basename "$_module") differ from the pass's: ${_why:-a feature is named twice}"
}

# listed <find arguments>: "<sha256>  <path>" per file found from the current folder, sorted by
# path component (as Python sorts paths: "a/x" before "a-b/x"), so the runner's own writer gives
# the same bytes. MANIFEST and SHA256SUMS are written with it.
listed() {
    find "$@" -type f | sed 's|^\./||' | sed 's|/|\x01|g' | LC_ALL=C sort | sed 's|\x01|/|g' \
        | xargs -d '\n' sha256sum --
}

# listed_tarballs <list>: the first field of each line of a recipes/cpan-* list that is neither
# blank nor a comment.
listed_tarballs() {
    awk '!/^#/ && NF { print $1 }' "$1"
}

# dist_of <tarball>: the distribution's name and version, the tarball's name without .tar.gz or
# .tgz.
dist_of() {
    printf '%s\n' "$1" | sed -e 's/\.tar\.gz$//' -e 's/\.tgz$//'
}

# unpack_pinned <name> <dest> <log>: unpacks downloads/<name> as the new folder <dest>. Nothing
# is made before the tarball matches its pin, and the bytes unpacked are a copy checked again,
# so what is checked is what is unpacked. A tarball must hold exactly one top folder, and <dest>
# must not exist; either is refused rather than merged. Only files and folders are taken: a
# link left in place would be followed by whatever reads the tree later, out of it.
unpack_pinned() {
    check_pinned "$1"
    [ ! -e "$2" ] && [ ! -L "$2" ] || fail "$2 already exists; $1 would replace it"
    _work="$2.unpack"
    rm -rf "$_work"
    mkdir -p "$_work/top"
    cp "$DOWNLOADS/$1" "$_work/tarball"
    if ! echo "$_pin  $_work/tarball" | sha256sum -c --status; then
        rm -rf "$_work"
        fail "$1 does not match its SHA-256 in downloads/SHA256SUMS"
    fi
    if ! tar -C "$_work/top" --no-same-owner --no-same-permissions -xzf "$_work/tarball" >>"$3" 2>&1; then
        rm -rf "$_work"
        fail "tar could not unpack $1 (see $3)"
    fi
    if [ -n "$(find "$_work/top" ! -type f ! -type d)" ]; then
        rm -rf "$_work"
        fail "$1 holds an entry that is neither a file nor a folder"
    fi
    _top=$(ls -A "$_work/top")
    if [ "$(printf '%s\n' "$_top" | wc -l)" -ne 1 ] || [ -L "$_work/top/$_top" ] \
        || [ ! -d "$_work/top/$_top" ]; then
        rm -rf "$_work"
        fail "$1 does not unpack to one top folder"
    fi
    mv "$_work/top/$_top" "$2"
    rm -rf "$_work"
}
