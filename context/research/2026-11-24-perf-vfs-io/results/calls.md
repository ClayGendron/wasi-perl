| call | wasmtime's WASI (µs) | Python shadow forwarding to it (µs) | Python serving a mount (µs) |
|---|---|---|---|
| noop host function (Python) | 6.61 | 7.38 | 7.22 |
| fd_fdstat_get | 0.16 | 11.92 | 11.97 |
| fd_filestat_get | 51.99 | 88.36 | 15.89 |
| fd_seek | 0.18 | 12.58 | 14.72 |
| fd_pread 8 KiB | 26.84 | 59.03 | 18.12 |
| fd_pread 64 KiB (wasmtime answers at most 64 KiB) | 39.62 | 79.00 | 18.85 |
| fd_seek + fd_read 8 KiB | 25.35 | 74.38 | 32.82 |
| fd_seek + fd_read 64 KiB | 47.58 | 93.92 | 33.73 |
| fd_seek + fd_read 1 MiB | 233.71 | 577.61 | 83.96 |
| fd_seek + fd_read 5 MB | 1299.00 | 1973.72 | 426.70 |
