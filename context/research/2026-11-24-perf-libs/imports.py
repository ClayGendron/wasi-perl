"""Print a module's imports, one per line, so two builds can be compared. Usage: imports.py WASM"""
import sys

import wasmtime

m = wasmtime.Module.from_file(wasmtime.Engine(), sys.argv[1])
for i in m.imports:
    print(f"{i.module}.{i.name}")
