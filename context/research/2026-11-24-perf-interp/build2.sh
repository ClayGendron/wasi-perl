for v in cand nosbox mymalloc2; do
  start=$(date +%s)
  WASI_PERL_JOBS=4 flock /home/user/wasi-perl-wt/machine.lock sh $S/v/$v/scripts/20_target.sh > $S/v/$v/build.out 2>&1
  echo "$v rc=$? $(( $(date +%s) - start ))s $(tail -1 $S/v/$v/build.out)"
done
