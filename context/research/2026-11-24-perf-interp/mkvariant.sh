#!/bin/sh
# mkvariant.sh DIR SED-SCRIPT: a build root at DIR that differs from this checkout only in
# recipes/config.sh, edited by SED-SCRIPT, and shares its toolchain and native perl read-only.
# Build it with: WASI_PERL_JOBS=4 flock /home/user/wasi-perl-wt/machine.lock sh DIR/scripts/20_target.sh
set -eu
dir=$1 edit=$2
root=$(cd "$(dirname "$0")/../../.." && pwd)
rm -rf "$dir"
mkdir -p "$dir/build" "$dir/downloads"
cp -a "$root/scripts" "$root/recipes" "$dir/"
cp "$root/downloads/SHA256SUMS" "$dir/downloads/"
ln -s /home/user/wasi-perl-downloads/dl/perl-5.42.3.tar.xz "$dir/downloads/"
for d in tools native native-install; do ln -s "$root/build/$d" "$dir/build/$d"; done
cp "$root/build/.native.stamp" "$dir/build/"
sed -i -e "$edit" "$dir/recipes/config.sh"
diff -u "$root/recipes/config.sh" "$dir/recipes/config.sh" >"$dir/config.diff" || true
[ -s "$dir/config.diff" ] || { echo "mkvariant: the edit changed nothing" >&2; exit 1; }
