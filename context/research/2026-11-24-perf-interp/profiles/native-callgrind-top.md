## sum_csv (valgrind --tool=callgrind, native perl 5.42.3 -O2, x86-64)

```
770,930,134 (100.0%)  PROGRAM TOTALS
Ir                    file:function
--------------------------------------------------------------------------------
101,088,146 (13.11%)  Perl_pp_split 
 67,206,310 ( 8.72%)  Perl_sv_clear 
 47,211,017 ( 6.12%)  Perl_newSV_type 
 43,210,993 ( 5.61%)  _int_free 
 36,042,282 ( 4.68%)  malloc 
 33,639,735 ( 4.36%)  Perl_sv_gets 
 27,208,058 ( 3.53%)  Perl_sv_setpvn_fresh.part.0 
 26,400,187 ( 3.42%)  Perl_do_readline 
 24,239,612 ( 3.14%)  Perl_sv_upgrade 
 24,001,075 ( 3.11%)  Perl_newSVpvn_flags 
 22,403,564 ( 2.91%)  free 
 21,800,000 ( 2.83%)  S_do_chomp 
 20,599,988 ( 2.67%)  Perl_pp_add 
 20,599,918 ( 2.67%)  Perl_av_clear 
 19,387,475 ( 2.51%)  Perl_grok_number_flags 
 19,201,664 ( 2.49%)  Perl_sv_free2 
 17,600,049 ( 2.28%)  Perl_pp_gvsv 
 17,293,533 ( 2.24%)  __memcpy_avx_unaligned_erms 
 16,018,800 ( 2.08%)  Perl_safesysmalloc 
 15,200,051 ( 1.97%)  Perl_pp_nextstate 
 13,600,086 ( 1.76%)  Perl_runops_standard 
```

## grep_log (valgrind --tool=callgrind, native perl 5.42.3 -O2, x86-64)

```
354,259,566 (100.0%)  PROGRAM TOTALS
Ir                   file:function
--------------------------------------------------------------------------------
53,171,476 (15.01%)  Perl_fbm_instr 
34,253,491 ( 9.67%)  Perl_sv_gets 
29,942,864 ( 8.45%)  Perl_pp_match 
26,400,187 ( 7.45%)  Perl_do_readline 
25,800,016 ( 7.28%)  Perl_re_intuit_start 
21,828,693 ( 6.16%)  Perl_regexec_flags 
20,590,555 ( 5.81%)  __memchr_avx2 
 9,542,881 ( 2.69%)  Perl_pp_and 
 9,352,475 ( 2.64%)  _int_malloc 
 8,397,308 ( 2.37%)  S_regmatch 
 7,428,662 ( 2.10%)  Perl_runops_standard 
 7,400,036 ( 2.09%)  Perl_pp_defined 
 7,200,036 ( 2.03%)  Perl_pp_readline 
 5,600,002 ( 1.58%)  Perl_pp_unstack 
 5,028,633 ( 1.42%)  Perl_pp_gvsv 
 5,000,051 ( 1.41%)  Perl_pp_nextstate 
 4,375,911 ( 1.24%)  Perl_leave_scope 
 4,000,020 ( 1.13%)  PerlIO_isutf8 
 3,761,065 ( 1.06%)  malloc_consolidate 
 3,463,648 ( 0.98%)  PerlIOBuf_get_cnt 
 3,416,111 ( 0.96%)  _int_free 
```

## inplace (valgrind --tool=callgrind, native perl 5.42.3 -O2, x86-64)

```
438,190,094 (100.0%)  PROGRAM TOTALS
Ir                   file:function
--------------------------------------------------------------------------------
36,608,336 ( 8.35%)  Perl_sv_gets 
35,800,128 ( 8.17%)  Perl_pp_subst 
29,371,516 ( 6.70%)  Perl_re_intuit_start 
26,400,187 ( 6.02%)  Perl_do_readline 
26,228,676 ( 5.99%)  Perl_fbm_instr 
21,200,053 ( 4.84%)  Perl_pp_enter 
20,600,000 ( 4.70%)  Perl_pp_print 
18,914,352 ( 4.32%)  Perl_regexec_flags 
15,600,039 ( 3.56%)  Perl_pp_leave 
14,851,195 ( 3.39%)  PerlIOBuf_write 
14,400,051 ( 3.29%)  Perl_runops_standard 
13,542,181 ( 3.09%)  __memchr_avx2 
10,800,000 ( 2.46%)  Perl_do_print 
10,000,025 ( 2.28%)  Perl_pp_nextstate 
 8,800,027 ( 2.01%)  Perl_pp_gvsv 
 7,400,036 ( 1.69%)  Perl_pp_defined 
 7,200,036 ( 1.64%)  Perl_pp_readline 
 6,663,524 ( 1.52%)  __memcpy_avx_unaligned_erms 
 6,000,020 ( 1.37%)  PerlIO_isutf8 
 5,972,025 ( 1.36%)  Perl_sv_catpvn_flags 
 5,657,146 ( 1.29%)  Perl_pp_unstack 
```

## loop (valgrind --tool=callgrind, native perl 5.42.3 -O2, x86-64)

```
335,939,521 (100.0%)  PROGRAM TOTALS
Ir                   file:function
--------------------------------------------------------------------------------
86,000,072 (25.60%)  Perl_pp_iter 
60,000,000 (17.86%)  Perl_pp_multiply 
59,000,000 (17.56%)  Perl_pp_add 
44,000,000 (13.10%)  Perl_pp_gvsv 
29,000,024 ( 8.63%)  Perl_pp_padsv 
28,000,083 ( 8.33%)  Perl_runops_standard 
28,000,000 ( 8.33%)  Perl_pp_unstack 

```

## json (valgrind --tool=callgrind, native perl 5.42.3 -O2, x86-64)

```
6,864,729,309 (100.0%)  PROGRAM TOTALS
Ir                    file:function
--------------------------------------------------------------------------------
440,497,902 ( 6.42%)  Perl_pp_entersub 
398,332,697 ( 5.80%)  Perl_pp_padsv 
398,282,437 ( 5.80%)  Perl_sv_setsv_flags 
380,502,993 ( 5.54%)  Perl_regexec_flags 
312,284,591 ( 4.55%)  Perl_pp_nextstate 
297,674,317 ( 4.34%)  Perl_runops_standard 
273,006,714 ( 3.98%)  Perl_pp_match 
264,418,128 ( 3.85%)  Perl_leave_scope 
218,889,643 ( 3.19%)  Perl_pp_substr 
199,301,834 ( 2.90%)  S_regmatch 
196,625,592 ( 2.86%)  Perl_pp_leavesub 
192,293,580 ( 2.80%)  Perl_sv_eq_flags 
158,460,117 ( 2.31%)  Perl_pp_and 
150,315,594 ( 2.19%)  _int_free 
114,548,311 ( 1.67%)  Perl_sv_clear 
114,255,214 ( 1.66%)  Perl_pp_multiconcat 
109,743,494 ( 1.60%)  Perl_leave_adjust_stacks 
 99,588,689 ( 1.45%)  _int_malloc 
 93,390,783 ( 1.36%)  Perl_pp_enter 
 84,050,128 ( 1.22%)  Perl_pp_defined 
 83,145,720 ( 1.21%)  malloc 
```

## hello (valgrind --tool=callgrind, native perl 5.42.3 -O2, x86-64)

```
1,895,182 (100.0%)  PROGRAM TOTALS
Ir                file:function
--------------------------------------------------------------------------------
199,897 (10.55%)  _int_malloc 
127,816 ( 6.74%)  do_lookup_x 
116,631 ( 6.15%)  __GI___tunables_init 
113,312 ( 5.98%)  Perl_hv_common 
108,854 ( 5.74%)  perl_construct 
 79,429 ( 4.19%)  __memset_avx2_unaligned_erms 
 77,280 ( 4.08%)  _dl_relocate_object
 64,375 ( 3.40%)  getenv 
 55,724 ( 2.94%)  _dl_relocate_object
 53,338 ( 2.81%)  Perl_gv_fetchpvn_flags 
 41,518 ( 2.19%)  S_share_hek_flags 
 40,593 ( 2.14%)  malloc 
 40,519 ( 2.14%)  strcmp 
 39,964 ( 2.11%)  calloc 
 38,580 ( 2.04%)  Perl_sv_upgrade 
 37,621 ( 1.99%)  perl_parse 
 37,553 ( 1.98%)  _dl_lookup_symbol_x 
 30,793 ( 1.62%)  _dl_relocate_object 
 27,106 ( 1.43%)  Perl_sv_clean_objs 
 25,524 ( 1.35%)  _dl_lookup_symbol_x
 24,947 ( 1.32%)  sbox32_hash_with_state.constprop.0 
```

