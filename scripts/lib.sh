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
