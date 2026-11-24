for v in notaint mymalloc zaphod nosbox noperturb nocow nopmc; do
  start=$(date +%s)
  WASI_PERL_JOBS=4 flock /home/user/wasi-perl-wt/machine.lock sh $S/v/$v/scripts/20_target.sh > $S/v/$v/build.out 2>&1
  echo "$v rc=$? $(( $(date +%s) - start ))s $(tail -1 $S/v/$v/build.out)"
done
  WASI_PERL_JOBS=4 flock /home/user/wasi-perl-wt/machine.lock sh $S/v/no64/scripts/20_target.sh > $S/v/no64/build.out 2>&1; echo "no64 rc=$?"
  WASI_PERL_JOBS=4 flock /home/user/wasi-perl-wt/machine.lock sh $S/v/cand/scripts/20_target.sh > $S/v/cand/build.out 2>&1; echo "cand rc=$?"
