"""Run one Perl script from a host folder under wasi-perl. Usage: run1.py FOLDER SCRIPT [ARG...]

The folder is granted read-only at /p and is the working folder.
"""

import sys
from pathlib import Path

import wasi_perl

here = Path(sys.argv[1])
r = wasi_perl.run(["/p/" + sys.argv[2], *sys.argv[3:]], grants=[wasi_perl.Grant(str(here), "/p")], cwd="/p")
sys.stdout.write(r.stdout.decode())
sys.stderr.write(r.stderr.decode())
