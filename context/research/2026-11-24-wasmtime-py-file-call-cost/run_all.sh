#!/bin/sh
# Every timing of the memo, in one go, to be run under the machine lock:
#   flock /home/user/wasi-perl-wt/machine.lock run_all.sh <python> <wasmtime> <probe.wasm> <data> <patched lib dir> <handoff binary> <results dir>
set -eu
py=$1 wt=$2 probe=$3 data=$4 lib=$5 handoff=$6 out=$7
here=$(dirname "$0")
K=read,eof,fstat,fdstat,pstat,open,clock
N=20000 R=7
echo "# py stock"; "$py" "$here/cost.py" "$probe" "$data" $N $R "$out/py-stock.jsonl" --kinds $K
echo "# py patched, flag off"; "$py" "$here/cost.py" "$probe" "$data" $N $R "$out/py-patched-off.jsonl" --kinds $K --lib "$lib"
echo "# py patched, flag on"; "$py" "$here/cost.py" "$probe" "$data" $N $R "$out/py-patched-on.jsonl" --kinds $K --lib "$lib" --blocking
echo "# py patched, flag set after the preopen"; "$py" "$here/cost.py" "$probe" "$data" $N $R "$out/py-patched-late.jsonl" --kinds read,fstat,pstat,open --lib "$lib" --blocking-late
echo "# py stock, pinned to one CPU"; taskset -c 0 "$py" "$here/cost.py" "$probe" "$data" $N $R "$out/py-stock-1cpu.jsonl" --kinds read,fstat
echo "# cli"; "$here/cli.sh" "$wt" "$probe" "$data" $N $R $K | tee "$out/cli.jsonl"
echo "# cli with a timeout"; TIMEOUT=3600s "$here/cli.sh" "$wt" "$probe" "$data" $N $R $K | tee "$out/cli-timeout.jsonl"
echo "# native hand-off, 4 CPUs then 1"; for i in 1 2 3; do "$handoff" 20000; done; for i in 1 2 3; do taskset -c 0 "$handoff" 20000; done
echo "# fuel"; "$py" "$here/cost.py" "$probe" "$data" 2010 1 "$out/fuel.jsonl" --kinds $K --fuel | tail -1
