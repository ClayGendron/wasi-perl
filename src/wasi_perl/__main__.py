"""`python -m wasi_perl run …` is `wasi-perl run …`."""

from wasi_perl.cli import main

raise SystemExit(main())
