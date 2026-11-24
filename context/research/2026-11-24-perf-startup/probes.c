/* Research only: marks around Perl's startup phases, for a host that reads fuel at each mark.
 *
 * The link wraps each function below (-Wl,--wrap=<name>); every wrapper calls the host's
 * wasi_perl_research.mark(id) on entry (2*id) and on return (2*id+1), then the real function.
 * The host records the store's remaining fuel at each mark, so the difference between two marks
 * is the guest instructions spent between them. Never shipped: it adds an import.
 *
 * Perl is built without MULTIPLICITY here, so pTHX is empty and the prototypes are plain. */
#include <stdint.h>

__attribute__((import_module("wasi_perl_research"), import_name("mark"))) void mark(int32_t id);

#define WRAP_VOID1(id, name, T1)                                                                  \
    void __real_##name(T1);                                                                       \
    void __wrap_##name(T1 a)                                                                      \
    {                                                                                             \
        mark(2 * (id));                                                                           \
        __real_##name(a);                                                                         \
        mark(2 * (id) + 1);                                                                       \
    }
#define WRAP_RET0(id, R, name)                                                                    \
    R __real_##name(void);                                                                        \
    R __wrap_##name(void)                                                                         \
    {                                                                                             \
        mark(2 * (id));                                                                           \
        R r = __real_##name();                                                                    \
        mark(2 * (id) + 1);                                                                       \
        return r;                                                                                 \
    }
#define WRAP_VOID0(id, name)                                                                      \
    void __real_##name(void);                                                                     \
    void __wrap_##name(void)                                                                      \
    {                                                                                             \
        mark(2 * (id));                                                                           \
        __real_##name();                                                                          \
        mark(2 * (id) + 1);                                                                       \
    }
#define WRAP_RET1(id, R, name, T1)                                                                \
    R __real_##name(T1);                                                                          \
    R __wrap_##name(T1 a)                                                                         \
    {                                                                                             \
        mark(2 * (id));                                                                           \
        R r = __real_##name(a);                                                                   \
        mark(2 * (id) + 1);                                                                       \
        return r;                                                                                 \
    }

/* 1 main, 2 perl_alloc, 3 perl_construct, 4 perl_parse, 5 perl_run, 6 perl_destruct,
 * 7 perl_free, 8 Perl_get_hash_seed, 9 Perl_init_i18nl10n, 10 Perl_seed, 11 Perl_yyparse,
 * 12 Perl_boot_core_PerlIO, 13 Perl_pp_require, 14 Perl_boot_core_UNIVERSAL (more below) */
int __real___main_argc_argv(int, char **);
int __wrap___main_argc_argv(int argc, char **argv)
{
    mark(2);
    int r = __real___main_argc_argv(argc, argv);
    mark(3);
    return r;
}
WRAP_RET0(2, void *, perl_alloc)
WRAP_VOID1(3, perl_construct, void *)
int __real_perl_parse(void *, void *, int, char **, char **);
int __wrap_perl_parse(void *p, void *x, int argc, char **argv, char **env)
{
    mark(8);
    int r = __real_perl_parse(p, x, argc, argv, env);
    mark(9);
    return r;
}
WRAP_RET1(5, int, perl_run, void *)
WRAP_RET1(6, int, perl_destruct, void *)
WRAP_VOID1(7, perl_free, void *)
WRAP_VOID1(8, Perl_get_hash_seed, unsigned char *)
WRAP_RET1(9, int, Perl_init_i18nl10n, int)
WRAP_RET0(10, uint32_t, Perl_seed)
WRAP_RET1(11, int, Perl_yyparse, int)
WRAP_VOID0(12, Perl_boot_core_PerlIO)
WRAP_RET0(13, void *, Perl_pp_require)
WRAP_VOID0(14, Perl_boot_core_UNIVERSAL)

/* 15 Perl_boot_core_builtin, 16 Perl_boot_core_mro, 17 Perl_newXS, 18 Perl_newXS_deffile */
WRAP_VOID0(15, Perl_boot_core_builtin)
WRAP_VOID0(16, Perl_boot_core_mro)
void *__real_Perl_newXS(const char *, void *, const char *);
void *__wrap_Perl_newXS(const char *n, void *a, const char *f)
{
    mark(34);
    void *r = __real_Perl_newXS(n, a, f);
    mark(35);
    return r;
}
void *__real_Perl_newXS_deffile(const char *, void *);
void *__wrap_Perl_newXS_deffile(const char *n, void *a)
{
    mark(36);
    void *r = __real_Perl_newXS_deffile(n, a);
    mark(37);
    return r;
}
