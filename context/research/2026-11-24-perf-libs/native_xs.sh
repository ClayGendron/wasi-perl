#!/bin/sh
# Build the candidate XS distributions against the native perl 5.42.3, the baseline, so the
# corpus can time each module natively too. Usage: native_xs.sh NATIVE_PERL SRC_DIR WORK_DIR
# SRC_DIR holds the unpacked tarballs (verified against PAUSE's CHECKSUMS); they are copied to
# WORK_DIR and built there, then installed under WORK_DIR/inst. Output goes to WORK_DIR/log.
set -eu
perl=$1 src=$2 work=$3
mkdir -p "$work/inst"
: >"$work/log"
for d in Types-Serialiser-1.01 common-sense-3.75 Cpanel-JSON-XS-4.83 JSON-XS-4.06 Text-CSV_XS-1.65 \
    Sort-Key-1.33 Unicode-UTF8-0.75 List-MoreUtils-XS-0.430 YAML-LibYAML-v0.910.0; do
    rm -rf "$work/$d"
    cp -a "$src/$d" "$work/$d"
    if (cd "$work/$d" && PERL5LIB="$work/inst/lib/perl5" "$perl" Makefile.PL INSTALL_BASE="$work/inst" \
        && make -j4 && make install) >>"$work/log" 2>&1 </dev/null; then
        echo "ok $d"
    else
        echo "FAILED $d"
    fi
done
