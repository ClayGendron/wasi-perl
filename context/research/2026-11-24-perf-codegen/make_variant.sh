#!/bin/sh
# make_variant.sh NAME OPTIMIZE [EXTRA_LDFLAGS] [STACK_BYTES]
# Writes variants/NAME.diff: recipes/config.sh with optimize='OPTIMIZE' and EXTRA_LDFLAGS added
# to ldflags, and recipes/wasi-cc with another stack size. Leaves recipes/ as it found them.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../../.." && pwd)
name=$1 opt=$2 ld=${3:-} stack=${4:-}
cd "$root"
git diff --quiet -- recipes || { echo "recipes/ has changes" >&2; exit 1; }
sed -i "s|^optimize='-O2'$|optimize='$opt'|" recipes/config.sh
[ -z "$ld" ] || sed -i "s|^ldflags='\(.*\)'$|ldflags='\1 $ld'|" recipes/config.sh
[ -z "$stack" ] || sed -i "s|stack-size=8388608|stack-size=$stack|" recipes/wasi-cc
git diff -- recipes >"$here/variants/$name.diff"
git checkout -q -- recipes
echo "variants/$name.diff"
