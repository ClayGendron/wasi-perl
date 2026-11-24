/* Research prototype: perl.wasm as a reactor that `wasmtime wizer` can snapshot.
 *
 *   wizer.initialize   run once when the snapshot is made: perl_alloc and perl_construct, and,
 *                      when PRELOAD is defined, a first perl_parse of `-e 0` with each module of
 *                      PRELOAD loaded by `-m` (require, no import), so that the snapshot holds the
 *                      compiled modules.
 *   run                one program per instance: argv and the environment from this instance's
 *                      WASI, then perl_parse, perl_run, perl_destruct and exit, as perlmain.c.
 *
 * What a snapshot must not keep is undone at the top of run:
 *   - wasi-libc's environment, read while initialising, is read again from this instance;
 *   - wasi-libc's preopen table and the guest's descriptor table are emptied, so the grants of
 *     this instance are found afresh, by name;
 *   - the working directory is set from PWD again (wasi-stubs.c does that before main);
 *   - Perl's internal random state, its hash-order bits and srand's state are seeded again, from
 *     the same source perl_construct uses;
 *   - without PRELOAD, when no hash yet holds a key, the hash seed and the hash function's
 *     state are made again as perl_construct makes them, so nothing hashed is frozen.
 *
 * With PRELOAD the hash seed cannot change: every stash and every shared key in the snapshot
 * was hashed with it. A second perl_parse would also start from a new, empty main stash and so
 * drop every preloaded package; the link wraps Perl_newSVpvn_share, which init_main_stash calls
 * first, to put the snapshot's main stash back in place of the new one.
 *
 * The file includes Perl's own perlmain.c, for its xs_init, with its main renamed away. It is
 * compiled with the command Perl's own objects get (see build.sh). */
#define main wasi_perl_unused_main
#include "perlmain.c"
#undef main

#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <wasi/api.h>
#include <wasi/libc-environ.h>
#include <wasi/libc.h>

void wasi_perl_fd_table_reset(void); /* fdtable-reset.c */

static HV *keep_stash; /* armed by run, taken by the first newSVpvn_share("main") */

SV *__real_Perl_newSVpvn_share(const char *s, I32 len, U32 hash);
SV *__wrap_Perl_newSVpvn_share(const char *s, I32 len, U32 hash)
{
    if (keep_stash != NULL && len == 4 && memcmp(s, "main", 4) == 0) {
        HV *fresh = PL_defstash; /* init_main_stash's new HV: its own reference and one more */
        PL_curstash = PL_defstash = keep_stash;
        keep_stash = NULL;
        SvREFCNT_dec(fresh);
        SvREFCNT_dec(fresh);
    }
    return __real_Perl_newSVpvn_share(s, len, hash);
}

static int empty(HV *hv)
{
    return hv == NULL || HvTOTALKEYS(hv) == 0;
}

/* What perl_construct does for the seeds, in its order, read from this instance's environment
 * and clock: srand's override, the internal random state, then the hash seed and its state when
 * nothing is hashed yet, else only the hash-order bits. */
static void reseed(int whole)
{
    UV n = 0;
    const char *e;
    if ((e = PerlEnv_getenv("PERL_RAND_SEED")) && grok_number(e, strlen(e), &n) == IS_NUMBER_IN_UV) {
        PL_srand_override_next = n;
        PERL_SRAND_OVERRIDE_NEXT_INIT();
    }
    if (!(e = PerlEnv_getenv("PERL_INTERNAL_RAND_SEED")) ||
        grok_number(e, strlen(e), &n) != IS_NUMBER_IN_UV)
        n = Perl_seed(aTHX);
    Perl_drand48_init_r(&PL_internal_random_state, (U32)n);
    if (whole) {
        Perl_get_hash_seed(aTHX_ PL_hash_seed);
        PERL_HASH_SEED_STATE(PERL_HASH_SEED, PL_hash_state);
    }
    else {
        if ((e = PerlEnv_getenv("PERL_PERTURB_KEYS"))) {
            if (strEQ(e, "0") || strEQ(e, "NO"))
                PL_hash_rand_bits_enabled = 0;
            else if (strEQ(e, "1") || strEQ(e, "RANDOM"))
                PL_hash_rand_bits_enabled = 1;
            else if (strEQ(e, "2") || strEQ(e, "DETERMINISTIC"))
                PL_hash_rand_bits_enabled = 2;
        }
        if (PL_hash_rand_bits_enabled == 1)
            PL_hash_rand_bits = Perl_seed(aTHX);
        if (!PL_hash_rand_bits)
            PL_hash_rand_bits = 0x8110ba9d;
    }
    PL_srand_called = FALSE;
}

/* The second parse finds every core XSUB, and every static extension's bootstrap, already in
 * the kept main stash; making them again would only redefine them, which -w reports. */
static int second_parse;
#define SKIP_ON_SECOND(name)                                                                      \
    void __real_##name(void);                                                                     \
    void __wrap_##name(void)                                                                      \
    {                                                                                             \
        if (!second_parse)                                                                        \
            __real_##name();                                                                      \
    }
/* parse_body makes PL_isarev, the map from each class to the classes that inherit from it, anew
 * just before boot_core_PerlIO; the preloaded classes' entries are put back there. */
static HV *keep_isarev;
void __real_Perl_boot_core_PerlIO(void);
void __wrap_Perl_boot_core_PerlIO(void)
{
    if (!second_parse) {
        __real_Perl_boot_core_PerlIO();
        return;
    }
    if (keep_isarev != NULL) {
        HV *fresh = PL_isarev;
        PL_isarev = keep_isarev;
        keep_isarev = NULL;
        SvREFCNT_dec(fresh);
    }
}
SKIP_ON_SECOND(Perl_boot_core_UNIVERSAL)
SKIP_ON_SECOND(Perl_boot_core_builtin)
SKIP_ON_SECOND(Perl_boot_core_mro)
CV *__real_Perl_newXS(const char *name, XSUBADDR_t subaddr, const char *filename);
CV *__wrap_Perl_newXS(const char *name, XSUBADDR_t subaddr, const char *filename)
{
    CV *cv;
    if (second_parse && name != NULL && (cv = get_cv(name, 0)) != NULL && CvXSUB(cv) == subaddr)
        return cv;
    return __real_Perl_newXS(name, subaddr, filename);
}

static int hashes_empty;

__attribute__((export_name("wizer.initialize"))) void wizer_initialize(void)
{
    static int fake_argc = 1;
    static char *fake_argv_[] = {"perl", NULL};
    static char **fake_argv = fake_argv_;
    PERL_SYS_INIT3(&fake_argc, &fake_argv, &environ);
    PERL_SYS_FPU_INIT;
    my_perl = perl_alloc();
    perl_construct(my_perl);
    PL_perl_destruct_level = 0;
#ifdef PRELOAD
    {
        static char *pre[] = {"perl", PRELOAD, "-e", "0", NULL};
        int n = (int)(sizeof pre / sizeof pre[0]) - 1;
        if (perl_parse(my_perl, xs_init, n, pre, NULL) != 0)
            abort();
        if (PL_endav != NULL && av_count(PL_endav) != 0)
            abort(); /* a preloaded module's END block would run in every call */
    }
#endif
    hashes_empty = empty(PL_strtab) && empty(PL_registered_mros) && empty(PL_stashcache) &&
                   empty(PL_modglobal) && empty(PL_pidstatus);
    PerlIO_flush(NULL);
    wasi_perl_fd_table_reset();
    __wasilibc_reset_preopens();
    __wasilibc_deinitialize_environ();
}

__attribute__((export_name("wasi_perl_hashes_empty"))) int wasi_perl_hashes_empty(void)
{
    return hashes_empty;
}

__attribute__((export_name("run"))) void run(void)
{
    __wasilibc_initialize_environ();
    {
        const char *pwd = getenv("PWD");
        if (pwd && pwd[0] == '/')
            (void)chdir(pwd);
    }
    __wasi_size_t argc, size;
    if (__wasi_args_sizes_get(&argc, &size) != 0)
        abort();
    char **argv = calloc(argc + 1, sizeof(char *));
    char *buf = malloc(size);
    if (argv == NULL || buf == NULL || __wasi_args_get((uint8_t **)argv, (uint8_t *)buf) != 0)
        abort();
    reseed(hashes_empty);
#ifdef PRELOAD
    /* The second parse finds @INC empty and fills it as the first did; %INC keeps the
     * preloaded files, so their require is a no-op and only their import runs. */
    av_clear(GvAVn(PL_incgv));
    if (PL_e_script) {
        SvREFCNT_dec(PL_e_script);
        PL_e_script = NULL;
    }
    keep_stash = PL_defstash;
    SvREFCNT_inc_simple_void_NN(keep_stash);
    keep_isarev = PL_isarev;
    SvREFCNT_inc_simple_void_NN(keep_isarev);
    second_parse = 1;
#endif
    PL_exit_flags |= PERL_EXIT_DESTRUCT_END;
    if (!perl_parse(my_perl, second_parse ? NULL : xs_init, (int)argc, argv, NULL))
        perl_run(my_perl);
    int status = perl_destruct(my_perl);
    perl_free(my_perl);
    PERL_SYS_TERM();
    exit(status);
}
