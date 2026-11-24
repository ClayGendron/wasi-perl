cd /home/user/wasi-perl-wt/perf-interp
uv run python /home/user/wasi-perl-wt/perf-interp/context/research/2026-11-24-perf-interp/callcost.py $S/perl-relink.wasm $S/art-base $S/data /home/user/wasi-perl-wt/perf-interp/context/research/2026-11-24-perf-interp/results/callcost-base.json
for v in relink buf65536 buf262144 buf65536-memchr buf262144-memchr buf65536-swar relink; do
  echo "$v $(uv run python /home/user/wasi-perl-wt/perf-interp/context/research/2026-11-24-perf-interp/measure.py time $S/perl-$v.wasm $S/art-base $S/data /home/user/wasi-perl-wt/perf-interp/context/research/2026-11-24-perf-interp/results/time2-$v.json hello sum_csv grep_log inplace count_lines many_files json 2>&1 | grep -v UV_ | tr '\n' ' ')"
done
