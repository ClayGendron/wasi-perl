| workload | buffer | total WASI calls | fd_read | fd_write | other |
|---|---:|---:|---:|---:|---:|
| hello | 8 KiB | 42 | 0 | 1 | 41 |
| sum_csv | 8 KiB | 609 | 560 | 1 | 48 |
| sum_csv | 64 KiB | 120 | 71 | 1 | 48 |
| sum_csv | 256 KiB | 68 | 19 | 1 | 48 |
| grep_log | 8 KiB | 1297 | 1248 | 1 | 48 |
| grep_log | 64 KiB | 206 | 157 | 1 | 48 |
| grep_log | 256 KiB | 89 | 40 | 1 | 48 |
| inplace | 8 KiB | 2548 | 1248 | 1244 | 56 |
| inplace | 64 KiB | 369 | 157 | 156 | 56 |
| inplace | 256 KiB | 135 | 40 | 39 | 56 |
| json | 8 KiB | 465 | 196 | 1 | 268 |
| json | 64 KiB | 306 | 37 | 1 | 268 |
| json | 256 KiB | 291 | 22 | 1 | 268 |
| loop | 8 KiB | 42 | 0 | 1 | 41 |
| many | 8 KiB | 1838 | 400 | 1 | 1437 |
