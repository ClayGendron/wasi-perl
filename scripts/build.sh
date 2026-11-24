#!/bin/sh
# Build everything from the pinned inputs: download and verify, the native host perl, then
# perl.wasm and its library under build/artifacts. Each step skips itself when its output
# exists and its inputs are unchanged.
set -eu
here=$(dirname "$0")
"$here/00_download.sh"
"$here/10_native.sh"
"$here/20_target.sh"
"$here/30_library.sh"
