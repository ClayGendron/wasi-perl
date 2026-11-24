## sum_csv (wasmtime 51 --profile=guest, 100 us, 3 runs pooled)

samples: 2127
| function | self % | inclusive % |
|---|---:|---:|
| `Perl_runops_standard` | 17.0 | 99.2 |
| `Perl_pp_split` | 9.3 | 45.7 |
| `Perl_newSVpvn_flags` | 9.0 | 17.4 |
| `Perl_sv_clear` | 8.9 | 12.7 |
| `Perl_newSV_type` | 5.4 | 5.4 |
| `Perl_safesysfree` | 3.9 | 5.5 |
| `memchr` | 3.4 | 3.4 |
| `Perl_do_readline` | 2.4 | 17.3 |
| `Perl_sv_upgrade` | 2.4 | 2.4 |
| `Perl_av_clear` | 2.2 | 16.3 |
| `Perl_sv_gets` | 2.0 | 14.7 |
| `Perl_pp_gvsv` | 2.0 | 2.0 |
| `PerlIOBase_read` | 1.9 | 1.9 |
| `PerlIOBuf_set_ptrcnt` | 1.8 | 1.8 |
| `Perl_safesysmalloc` | 1.6 | 3.1 |
| `Perl_pp_nextstate` | 1.6 | 2.5 |
| `Perl_sv_2mortal` | 1.5 | 1.5 |
| `Perl_sv_free2` | 1.4 | 14.2 |
| `Perl_re_intuit_string` | 1.3 | 1.3 |
| `Perl_pp_readline` | 1.2 | 18.5 |

## grep_log (wasmtime 51 --profile=guest, 100 us, 3 runs pooled)

samples: 1150
| function | self % | inclusive % |
|---|---:|---:|
| `memchr` | 18.4 | 18.4 |
| `Perl_runops_standard` | 16.1 | 99.5 |
| `PerlIOBase_read` | 6.3 | 6.7 |
| `Perl_fbm_instr` | 6.0 | 15.8 |
| `Perl_pp_match` | 4.5 | 33.4 |
| `Perl_do_readline` | 4.5 | 38.3 |
| `Perl_sv_gets` | 3.8 | 33.7 |
| `PerlIOBuf_set_ptrcnt` | 3.6 | 3.6 |
| `Perl_re_intuit_start` | 3.2 | 18.1 |
| `Perl_pp_and` | 2.6 | 2.6 |
| `Perl_regexec_flags` | 2.3 | 28.9 |
| `Perl_mg_find` | 2.2 | 2.2 |
| `S_regmatch` | 2.1 | 3.9 |
| `PerlIOBuf_get_cnt` | 1.9 | 1.9 |
| `Perl_pp_readline` | 1.7 | 40.0 |
| `Perl_pp_nextstate` | 1.7 | 1.7 |
| `Perl_pp_gv` | 1.6 | 1.6 |
| `memcmp` | 1.4 | 1.4 |
| `Perl_pp_gvsv` | 1.4 | 1.4 |
| `Perl_pp_unstack` | 1.3 | 1.3 |

## inplace (wasmtime 51 --profile=guest, 100 us, 3 runs pooled)

samples: 1649
| function | self % | inclusive % |
|---|---:|---:|
| `Perl_runops_standard` | 19.2 | 98.8 |
| `memchr` | 10.3 | 10.3 |
| `PerlIOBase_noop_ok` | 6.6 | 6.6 |
| `Perl_mg_find` | 4.2 | 4.2 |
| `PerlIOBase_read` | 4.1 | 4.5 |
| `Perl_pp_subst` | 3.6 | 19.2 |
| `PerlIOBuf_write` | 3.3 | 9.8 |
| `Perl_do_readline` | 3.2 | 27.2 |
| `Perl_pp_gvsv` | 2.6 | 2.6 |
| `Perl_sv_gets` | 2.5 | 23.5 |
| `Perl_re_intuit_start` | 2.5 | 9.5 |
| `PerlIOBuf_set_ptrcnt` | 2.4 | 2.4 |
| `Perl_pp_enter` | 2.2 | 2.2 |
| `Perl_pp_leave` | 1.8 | 1.8 |
| `PerlIO_isutf8` | 1.7 | 1.7 |
| `Perl_pp_nextstate` | 1.6 | 1.6 |
| `Perl_newSVpvn_flags` | 1.5 | 2.0 |
| `Perl_fbm_instr` | 1.5 | 7.0 |
| `Perl_pp_defined` | 1.3 | 1.3 |
| `Perl_pp_readline` | 1.2 | 28.4 |

## loop (wasmtime 51 --profile=guest, 100 us, 3 runs pooled)

samples: 1276
| function | self % | inclusive % |
|---|---:|---:|
| `Perl_runops_standard` | 51.6 | 98.0 |
| `Perl_pp_gvsv` | 13.9 | 13.9 |
| `Perl_pp_iter` | 12.6 | 12.6 |
| `Perl_pp_multiply` | 5.9 | 5.9 |
| `Perl_pp_add` | 5.3 | 5.3 |
| `Perl_pp_unstack` | 5.2 | 5.2 |
| `Perl_pp_padsv` | 3.4 | 3.4 |
| `_start` | 0.2 | 100.0 |
| `__wasi_fd_prestat_get` | 0.2 | 0.2 |
| `is_open` | 0.2 | 0.2 |
| `Perl_hv_common` | 0.2 | 0.2 |
| `Perl_PerlIO_close` | 0.2 | 0.2 |
| `perl_construct` | 0.1 | 0.5 |
| `PerlIOBase_pushed` | 0.1 | 0.1 |
| `Perl_mro_method_changed_in` | 0.1 | 0.1 |
| `Perl_hv_common_key_len` | 0.1 | 0.2 |
| `Perl_pp_leaveloop` | 0.1 | 0.1 |
| `Perl_sv_clean_objs` | 0.1 | 0.1 |
| `__wasilibc_initialize_environ_eagerly` | 0.1 | 0.1 |
| `S_gv_init_svtype` | 0.1 | 0.1 |

## json (wasmtime 51 --profile=guest, 100 us, 3 runs pooled)

samples: 24605
| function | self % | inclusive % |
|---|---:|---:|
| `Perl_runops_standard` | 28.8 | 99.7 |
| `Perl_pp_padsv` | 6.6 | 6.7 |
| `Perl_pp_nextstate` | 4.3 | 5.6 |
| `Perl_sv_setsv_flags` | 3.0 | 4.6 |
| `Perl_leave_scope` | 3.0 | 4.9 |
| `Perl_regexec_flags` | 2.9 | 12.3 |
| `Perl_pp_entersub` | 2.9 | 3.1 |
| `Perl_pp_match` | 2.6 | 15.0 |
| `Perl_pp_and` | 2.4 | 2.4 |
| `Perl_sv_setpvn` | 1.8 | 1.8 |
| `S_regmatch` | 1.7 | 3.0 |
| `Perl_pp_const` | 1.7 | 1.7 |
| `Perl_sv_clear` | 1.5 | 2.6 |
| `Perl_pp_multiconcat` | 1.4 | 1.4 |
| `Perl_sv_eq_flags` | 1.3 | 2.0 |
| `Perl_pp_pushmark` | 1.3 | 1.3 |
| `Perl_pp_seq` | 1.3 | 3.1 |
| `S_cleanup_regmatch_info_aux` | 1.2 | 1.4 |
| `S_find_byclass` | 1.2 | 1.9 |
| `Perl_safesysfree` | 1.2 | 1.6 |

## Callers of the wasm-only hot spots

```
[grep_log]
memchr: 212 self samples
  58%  Perl_sv_gets < Perl_do_readline < Perl_pp_readline
  42%  Perl_fbm_instr < Perl_re_intuit_start < Perl_regexec_flags
PerlIOBase_read: 73 self samples
  100%  PerlIOBuf_read < PerlIO_getc < Perl_sv_gets
PerlIOBase_noop_ok: 1 self samples
  100%  PerlIOBuf_flush < Perl_PerlIO_flush < perl_destruct
[inplace]
memchr: 170 self samples
  64%  Perl_sv_gets < Perl_do_readline < Perl_pp_readline
  36%  Perl_fbm_instr < Perl_re_intuit_start < Perl_regexec_flags
PerlIOBase_read: 68 self samples
  100%  PerlIOBuf_read < PerlIO_getc < Perl_sv_gets
PerlIOBase_noop_ok: 109 self samples
  99%  PerlIOBuf_flush < PerlIOBuf_write < Perl_PerlIO_write
  1%  PerlIOBuf_flush < PerlIOBuf_fill < PerlIOBase_read
[sum_csv]
memchr: 73 self samples
  100%  Perl_sv_gets < Perl_do_readline < Perl_pp_readline
PerlIOBase_read: 40 self samples
  100%  PerlIOBuf_read < PerlIO_getc < Perl_sv_gets
PerlIOBase_noop_ok: 1 self samples
  100%  PerlIOBuf_flush < PerlIOBuf_fill < PerlIOBase_read
```
