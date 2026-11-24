| workload | WASI calls (grant) | of them: fd-table start / look | fd_read calls, bytes | fd_write calls | path_open | path_filestat_get | fd_readdir |
|---|---|---|---|---|---|---|---|
| hello | 36 | 10 / 1 | 0, 0 | 0 | 1 | 1 | 0 |
| ne_50k | 52 | 10 / 2 | 8, 50,022 | 1 | 2 | 2 | 0 |
| lane_5m | 656 | 10 / 2 | 612, 5,000,032 | 1 | 2 | 2 | 0 |
| slurp_5m | 658 | 10 / 2 | 612, 5,000,032 | 1 | 2 | 2 | 0 |
| pi_50k | 66 | 10 / 2 | 8, 50,022 | 7 | 3 | 2 | 0 |
| grep_args_500 | 4537 | 10 / 501 | 1000, 120,118 | 1 | 501 | 501 | 0 |
| grep_glob_500 | 5114 | 10 / 506 | 1007, 174,761 | 1 | 506 | 1036 | 4 |
| grep_opendir_500 | 4545 | 10 / 502 | 1000, 120,118 | 1 | 502 | 502 | 4 |
| write_new | 61 | 10 / 2 | 0, 0 | 18 | 2 | 2 | 0 |

| workload | a_wasi_python | memo | memo_defer | memo_defer_statread | all_four |
|---|---|---|---|---|---|
| hello | 0 () | 0 () | 0 () | 0 () | 0 () |
| ne_50k | 2 (read 1, stat 1) | 2 (read 1, stat 1) | 2 (read 1, stat 1) | 1 (read 1) | 1 (read 1) |
| lane_5m | 2 (read 1, stat 1) | 2 (read 1, stat 1) | 2 (read 1, stat 1) | 1 (read 1) | 1 (read 1) |
| slurp_5m | 2 (read 1, stat 1) | 2 (read 1, stat 1) | 2 (read 1, stat 1) | 1 (read 1) | 1 (read 1) |
| pi_50k | 6 (read 1, rename 1, stat 2, write 2) | 6 (read 1, rename 1, stat 2, write 2) | 5 (read 1, rename 1, stat 2, write 1) | 4 (read 2, rename 1, write 1) | 3 (read 2, write 1) |
| grep_args_500 | 1000 (read 500, stat 500) | 1000 (read 500, stat 500) | 1000 (read 500, stat 500) | 500 (read 500) | 500 (read 500) |
| grep_glob_500 | 1503 (list 1, read 500, stat 1002) | 1002 (list 1, read 500, stat 501) | 1002 (list 1, read 500, stat 501) | 502 (list 1, read 501) | 502 (list 1, read 501) |
| grep_opendir_500 | 1003 (list 1, read 500, stat 502) | 1002 (list 1, read 500, stat 501) | 1002 (list 1, read 500, stat 501) | 502 (list 1, read 501) | 502 (list 1, read 501) |
| write_new | 4 (stat 2, write 2) | 3 (stat 1, write 2) | 2 (stat 1, write 1) | 2 (read 1, write 1) | 2 (read 1, write 1) |

| workload | database round trips, (e) cache + (a) | (e) cache + all four switches |
|---|---|---|
| hello | 0 | 0 |
| ne_50k | 1 | 1 |
| lane_5m | 1 | 1 |
| slurp_5m | 1 | 1 |
| pi_50k | 5 | 3 |
| grep_args_500 | 1 | 1 |
| grep_glob_500 | 3 | 2 |
| grep_opendir_500 | 3 | 2 |
| write_new | 4 | 2 |
