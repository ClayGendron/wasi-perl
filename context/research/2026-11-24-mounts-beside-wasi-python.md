# Mounts beside wasi-python: the unification record

- **For:** specification 017, Acceptance 2. A later shared package should be a merge of two near-equal files, not a rewrite. This record lists every remaining difference between wasi-perl's mount code and wasi-python's at `f9bf678` (0.1.6), with its reason.
- **Labels:** EXECUTED means run and seen here. READ means read from a file. INFERRED means reasoned.

## 1. The adapted modules, line for line

Each count is `diff -u` of wasi-python's file at `f9bf678` against wasi-perl's, its `+` and `-` lines without the two headers (EXECUTED):

```
WP=/home/user/claygendron/wasi-python/src/wasi_python   # checked out at f9bf678
for f in mounts _fs_memory _fs_table _fs_descriptors _fs_paths _wasmtime_ffi; do
  diff -u $WP/$f.py src/wasi_perl/$f.py |
    awk -v f=$f 'NR>2 && /^\+/{a++} NR>2 && /^-/{d++} /^@@/{h++} END{print f".py: "h" hunks, +"a" -"d}'
done
```

| module | hunks | added | removed |
|---|---|---|---|
| `mounts.py` | 3 | 6 | 2 |
| `_fs_memory.py` | 4 | 9 | 10 |
| `_fs_table.py` | 12 | 20 | 84 |
| `_fs_descriptors.py` | 5 | 10 | 14 |
| `_fs_paths.py` | 8 | 11 | 96 |
| `_wasmtime_ffi.py` | 7 | 56 | 200 |

Every hunk, with its reason. "Package name" means `wasi_python` became `wasi_perl` and nothing else changed.

### `mounts.py`

1. `-1,6`: package name, in the module docstring's example.
2. `-18,6`: the docstring's adaptation note.
3. `-179,7`: `Mount.guest`'s docstring drops "(not with `root=`)": wasi-perl has no `root=`.

### `_fs_memory.py`

1. `-4,6`: the adaptation note.
2. `-14,8`: the comment on `SIGNATURES` names perl.wasm's imports (22 of the 29) in place of wasi-python's binaries.
3. `-49,9`: the comment on `FORWARD_SAFE`: `poll_oneoff` is wasmtime's here, not the runner's.
4. `-62,11`: `COUNTED` is trimmed: it serves wasi-python's cap on open files on a call without mounts.

### `_fs_table.py`

1. `-1,6`: the docstring drops "the cap on files open at once" (trimmed feature: `max_open_files`).
2. `-10,15`: the docstring drops the counted linker and the cap (trimmed features), and adds the adaptation note.
3. `-31,8`: package name.
4. `-53,7`: package name.
5. `-87,10`: `__init__` drops the `open_files` keyword, and its comment the session's turns (trimmed: `max_open_files`, sessions).
6. `-126,42`: `open_files`, `held`, `session`, `preopens`, `open_now`, `kind`, `_at_cap` and `new_turn` are trimmed (`max_open_files`; the host-side `poll_oneoff`; sessions).
7. `-171,7`: `walk` drops its `ending` keyword (sessions).
8. `-180,32`: `walk`'s session paragraph and session branches are trimmed (sessions).
9. `-227,7`: package name, in `bind`.
10. `-250,7`: package name, in `forward`.
11. `-294,7`: package name, in `record_stop`.
12. `-604,21`: `_hold`, `_unhold` and `_renumbered` are trimmed (`max_open_files`).

### `_fs_descriptors.py`

1. `-3,6`: the adaptation note.
2. `-11,7`: package name.
3. `-50,10`: package name, twice.
4. `-74,10`: `fd_close` on a number of wasmtime's no longer updates the held set (`max_open_files`).
5. `-398,18`: `fd_renumber` no longer updates the held set, and its docstring drops it (`max_open_files`).

### `_fs_paths.py`

1. `-2,6`: the adaptation note.
2. `-10,11`, 3. `-27,13`, 4. `-44,11`: package name, and the seven imports only the trimmed look used are dropped: `DIRECTORY`, `ENXIO`, `FILESTAT_SIZE`, `FILETYPE_AT`, `REGULAR_FILE`, `RIGHTS` and `SYMBOLIC_LINK`.
5. `-59,10`: package name, twice.
6. `-114,7`: a `path_open` on a number of wasmtime's goes straight to `files.forward`: wasi-perl's look before an open is the guest's own (spec 010), and it has no cap on open files.
7. `-143,7`: the mount-side open drops `or files._at_cap()` (`max_open_files`).
8. `-184,90`: `_forwarded_open`, `_look`, `_looked_at` and `_refusal` are trimmed: the cap and the host-side look.

### `_wasmtime_ffi.py`

This module grows from wasi-perl's own three operations; wasi-python's holds every private name its runner calls.

1. `-1,91`: the docstring: wasi-perl's, shorter, with the adaptation note. wasi-python's lists a debt sheet of 32 private names, most for features wasi-perl lacks. And the imports: wasi-perl imports `Callable` for `on_epoch`.
2. `-93,53`: the imports drop `Config`, `_func` and `_wasi`, which only trimmed operations read; `__all__` lists the names wasi-perl keeps, with its own `error`, `on_epoch` and `set_stdin_bytes`; `NEEDED` begins.
3. `-148,31`: `NEEDED` is cut to the private names this module reaches: 21 of wasi-python's 31, and `wasm_byte_vec_t`, `wasm_byte_vec_new` and `wasi_config_set_stdin_bytes`, which `set_stdin_bytes` reaches. `OPTIONAL` is trimmed (the async stack). `version` imports `importlib.metadata` inside the function, which runs only for a refusal: that import is one of the slowest of a process's first call (spec 014). wasi-python could adopt it unchanged.
4. `-202,9`: package name, in `check`'s message.
5. `-212,17`: the call of `check` names only the holders this module reads.
6. `-289,72`: `new_error`, `delete_error`, `EPOCH_CALLBACK`, `watch_epochs`, the custom outputs, the async stack and `engine_symbol_address` are trimmed; wasi-perl's own `set_stdin_bytes`, `error` and `on_epoch` stand there.
7. `-362,23`: `last_exception`, `host_function_count` and `custom_output_table`, the judge's readers, are trimmed.

`Context`, `Storelike`, `context_of`, `context_address`, `set_epoch_deadline`, `get_fuel`, `set_fuel`, `raw_func` and `PreparedCall` are wasi-python's, line for line.

## 2. The glue, which wasi-python keeps elsewhere

These are adaptations, not copies: wasi-python's live in modules wasi-perl does not have (READ, `_call.py` and `_places.py` at `f9bf678`).

| wasi-perl | wasi-python | difference, and why |
|---|---|---|
| `runner._stack` | `_home._stack` | the same thread-local stack of calls |
| `runner._Call` | `_call._Call` | no door, poll, cancel, waits or home. Its `stopped` and `ended` live on the call's `_stop.Stop`, so the epoch callback, which is `_stop`'s, reads them |
| `runner._shared` | `_call._shared` | one shadow kind (`files`); a noted Ctrl-C stops the guest through the epoch callback instead of being raised inside the host function, since wasi-perl's handler only notes it (`_stop.watching_ctrl_c`) |
| `runner._Context` | `_call._Context` | `exiting` is `sys.is_finalizing()`; `ctrl_c_pending` the call's noted Ctrl-C; `cancelled` always `False`; `deadline_passed` reads `_engines.TICKER`; `on_home` calls directly, since the guest runs on the calling thread |
| `runner._drive` | `_call._drive` | instantiates from an `InstancePre` (spec 014), runs `_start`, reads the exit status export; the trampoline is bound after the instance and before the fuel cap, as in wasi-python |
| `_engines.linker`, `mount_linker`, `trampoline` | `_call._linker`, `_mount_linker`, `_trampoline_module` | no runner `poll_oneoff` or door on the linker; cached in `_engines` beside the engines |
| `_engines.linked(..., mounts=True)` | none | the mount linker's `InstancePre` per compiled module, as spec 014 made one for today's linker |
| `_grants.check_mounts` | `_places._check_mounts` | the same order. The guest path is wasi-perl's grant rule (no `\`); the reserved folders are `/usr/local/lib/perl5` and `/dev`, at or under; there is no `root=` rule |
| `_grants._siphash13` | `_places._siphash13` | the same function |
| `_grants._judge_cwd` | none | `cwd` may lie in a mount, by its text (Decision 7) |
| `_stop` | `_call._epoch_answer` | the callback learns a recorded stop and a named end; `"out of file calls"` is a third word |

## 3. Every difference a provider or a caller can see

As the specification's table, confirmed by the checks (EXECUTED: `uv run pytest tests/mounts`):

1. `cancel=`, `run_async`, `Session`, `max_open_files`, `Defaults.open_files`, `root=`, `import_path=` and tools are absent. Nothing wasi-perl has behaves differently.
2. A mount's guest path may not hold `\`, as a grant's may not. wasi-python could refuse it in `_places._guest_path`; its table already answers `EINVAL` for a `\` in a component.
3. The reserved folders are `/usr/local/lib/perl5` and `/dev`, where wasi-python's is `/usr/local/lib`.
4. `cwd` may lie in a mount, checked by text. The same rule fits wasi-python if it gains `cwd`.
5. The guest runs on the calling thread, so the provider is called directly. A provider sees the same thread.
6. `trapped` is never `"cancelled"`.
7. `poll_oneoff` is wasmtime's: the guest answers a mount file as ready itself, and a mount folder's subscription is `EBADF`.
8. Perl's own calls cost a provider more than Python's: every open but an exclusive create costs a `stat` before its `read` (the guest's look), `opendir` a `stat` more, `unlink` a `stat` before its `remove`, and `-i` an `opendir(".")` per file (`docs/mounts.md`, *What a Perl program costs a provider*).
9. `Defaults.file_calls` is the fourth field where it is wasi-python's fifth, since wasi-perl has no `async_workers`. By keyword, the two are the same.

The API check (`tests/mounts/test_mount_api.py::test_the_mount_api_is_wasi_pythons`) holds the public half: the eight types' blocks equal wasi-python's `tests/public_api.txt` line for line, `run`'s two lines equal, `Completed.files` right after `truncated`, and `Defaults` a subsequence of wasi-python's block (EXECUTED).
