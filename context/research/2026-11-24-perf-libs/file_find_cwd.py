"""File::Find under wasi-perl with and without a working folder. Usage: file_find_cwd.py DATA_DIR"""
import sys

import wasi_perl

g = [wasi_perl.Grant(sys.argv[1], "/data")]
cases = [
    (None, 'find(sub { $n++ if -f }, "/data/tree"); print "$n\\n"'),
    ("/data", 'find(sub { $n++ if -f }, "/data/tree"); print "$n\\n"'),
    (None, 'find({wanted => sub { $n++ if -f }, no_chdir => 1}, "/data/tree"); print "$n\\n"'),
]
for cwd, prog in cases:
    r = wasi_perl.run(["-MFile::Find", "-e", prog], grants=g, cwd=cwd)
    out, err = r.stdout.decode().strip(), r.stderr.decode().strip()
    print(f"cwd={cwd} no_chdir={'no_chdir' in prog}: exit {r.exit_code} stdout {out!r} stderr {err!r}")
