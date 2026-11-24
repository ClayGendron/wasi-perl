cd /home/user/wasi-perl-wt/perf-interp
uv run python /home/user/wasi-perl-wt/perf-interp/context/research/2026-11-24-perf-interp/wasicost.py $S/wasicost.wasm $S/art-base $S/data 5000 > /home/user/wasi-perl-wt/perf-interp/context/research/2026-11-24-perf-interp/results/wasicost.txt 2>&1
for r in 1 2 3; do for v in relink buf65536 memchr-simd buf65536-memchr buf65536-memchr-isatty; do
  echo "r$r $v $(uv run python /home/user/wasi-perl-wt/perf-interp/context/research/2026-11-24-perf-interp/measure.py time $S/perl-$v.wasm $S/art-base $S/data /home/user/wasi-perl-wt/perf-interp/context/research/2026-11-24-perf-interp/results/time3-r$r-$v.json hello sum_csv grep_log inplace count_lines many_files loop 2>&1 | grep -v UV_ | tr '\n' ' ')"
done; done
