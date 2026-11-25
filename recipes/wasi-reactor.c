/* perl as a WASI reactor, for preload images: a host runs a prelude once in an instance of this
 * module, captures its linear memory, and writes that memory into a new module (the image
 * module, src/wasi_perl/_image_module.py). Each call from the image is one fresh instance of
 * that module, entered at run.
 *
 * Exports (with -mexec-model=reactor):
 *   _initialize            wasi-libc's: the C constructors.
 *   prepare() -> i32       once per instance: argv and the environment from WASI, the clocks
 *                          pinned, perl_construct, then perl_parse and perl_run of that argv,
 *                          with END blocks kept for the end of every later call. Returns Perl's
 *                          status, or 0 once everything a later call must find afresh is
 *                          forgotten. The image module does not export it.
 *   run()                  once per instance of the image: a second perl_parse and perl_run of
 *                          this instance's argv over the kept interpreter, then perl_destruct
 *                          and exit, as perlmain.c ends.
 *   wasi_perl_open_files   how many descriptors the prelude left open, beyond stdio and the
 *                          preopened folders.
 *   wasi_perl_exit_status  (wasi-stubs.c) the status the guest gave exit.
 *
 * The second parse would start from an empty main stash, a new inheritance map and new core
 * XSUBs. The link wraps the functions that follow each of those steps so that the prelude's are
 * kept. Each wrapper checks that it finds what perl.c 5.42.3 makes at that point, and aborts
 * otherwise, so a Perl whose order differs traps instead of running wrong. The research
 * prototype of the preload image (context/research/2026-11-24-preload-image-prototype/) is where
 * this file was first written; it is adapted here.
 *
 * The file includes Perl's own generated perlmain.c, for its xs_init, with its main renamed away,
 * and is compiled with the command Perl's own objects get. */
#define main wasi_perl_command_main
#include "perlmain.c"
#undef main

#if PERL_REVISION != 5 || PERL_VERSION != 42 || PERL_SUBVERSION != 3
#error "wasi-reactor.c wraps perl.c's parse order as it is in 5.42.3; read it again for this Perl"
#endif

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <wasi/api.h>
#include <wasi/libc-environ.h>
#include <wasi/libc.h>
#include <wasi/version.h>

/* The working folder is reset through wasi-libc's own variable (forget_cwd, below), whose
 * meaning is that of wasi-libc's chdir.c and getcwd.c as wasi-sdk 34 ships them. */
#if __wasi_sdk_major__ != 34
#error "wasi-reactor.c resets wasi-libc's working folder as wasi-sdk 34 keeps it; read it again"
#endif

void wasi_perl_fd_table_reset(void);    /* wasi-fd-table.c */
int wasi_perl_fd_table_open_files(void); /* wasi-fd-table.c */

/* The stack pointer, read straight from its global. */
__asm__(".globaltype __stack_pointer, i32");
static uintptr_t stack_pointer(void)
{
    uintptr_t sp;
    __asm__ volatile("global.get __stack_pointer\n\tlocal.set %0" : "=r"(sp));
    return sp;
}

/* While the prelude runs, every clock answers one fixed instant (realtime 2026-01-01T00:00:00Z,
 * monotonic 0), so no time the image holds lies in a later call's future. Entropy is not pinned:
 * the hash seed drawn now must be the embedder's own. */
static int clocks_pinned;
__wasi_errno_t __real___wasi_clock_time_get(__wasi_clockid_t, __wasi_timestamp_t,
                                            __wasi_timestamp_t *);
__wasi_errno_t __wrap___wasi_clock_time_get(__wasi_clockid_t id, __wasi_timestamp_t precision,
                                            __wasi_timestamp_t *time)
{
    if (!clocks_pinned)
        return __real___wasi_clock_time_get(id, precision, time);
    *time = id == __WASI_CLOCKID_REALTIME ? (__wasi_timestamp_t)1767225600 * 1000000000u : 0;
    return __WASI_ERRNO_SUCCESS;
}

static char **wasi_argv(int *argc)
{
    __wasi_size_t n, size;
    if (__wasi_args_sizes_get(&n, &size) != 0)
        abort();
    char **argv = (char **)calloc(n + 1, sizeof(char *));
    char *buf = (char *)malloc(size ? size : 1);
    if (argv == NULL || buf == NULL || __wasi_args_get((uint8_t **)argv, (uint8_t *)buf) != 0)
        abort();
    *argc = (int)n;
    return argv;
}

/* The second parse's three undoings, each kept from happening. */
static int second_parse;
static HV *kept_stash;  /* taken by init_main_stash's newSVpvs_share("main") */
static HV *kept_isarev; /* put back where parse_body makes PL_isarev anew */

SV *__real_Perl_newSVpvn_share(pTHX_ const char *s, I32 len, U32 hash);
SV *__wrap_Perl_newSVpvn_share(pTHX_ const char *s, I32 len, U32 hash)
{
    if (kept_stash != NULL && len == 4 && memcmp(s, "main", 4) == 0) {
        /* init_main_stash has just made PL_defstash a new HV, held twice, and nothing else. */
        HV *fresh = PL_defstash;
        if (fresh == kept_stash || PL_curstash != fresh || SvREFCNT(fresh) != 2 ||
            HvTOTALKEYS(fresh) != 0)
            abort();
        PL_curstash = PL_defstash = kept_stash;
        kept_stash = NULL;
        SvREFCNT_dec(fresh);
        SvREFCNT_dec(fresh);
    }
    return __real_Perl_newSVpvn_share(aTHX_ s, len, hash);
}

void __real_Perl_boot_core_PerlIO(pTHX);
void __wrap_Perl_boot_core_PerlIO(pTHX)
{
    if (!second_parse) {
        __real_Perl_boot_core_PerlIO(aTHX);
        return;
    }
    /* parse_body made PL_isarev a new, empty HV just before this call. The stash must have been
     * put back by then, or the order is not 5.42.3's. */
    if (kept_isarev == NULL || kept_stash != NULL || PL_isarev == kept_isarev ||
        HvTOTALKEYS(PL_isarev) != 0)
        abort();
    HV *fresh = PL_isarev;
    PL_isarev = kept_isarev;
    kept_isarev = NULL;
    SvREFCNT_dec(fresh);
}

#define ONCE_ONLY(name)                                                                           \
    void __real_##name(pTHX);                                                                     \
    void __wrap_##name(pTHX);                                                                     \
    void __wrap_##name(pTHX)                                                                      \
    {                                                                                             \
        if (!second_parse)                                                                        \
            __real_##name(aTHX);                                                                  \
    }
ONCE_ONLY(Perl_boot_core_UNIVERSAL)
ONCE_ONLY(Perl_boot_core_builtin)
ONCE_ONLY(Perl_boot_core_mro)

CV *__real_Perl_newXS(pTHX_ const char *name, XSUBADDR_t subaddr, const char *filename);
CV *__wrap_Perl_newXS(pTHX_ const char *name, XSUBADDR_t subaddr, const char *filename)
{
    CV *cv;
    if (second_parse && name != NULL && (cv = get_cv(name, 0)) != NULL && CvISXSUB(cv) &&
        CvXSUB(cv) == subaddr)
        return cv;
    return __real_Perl_newXS(aTHX_ name, subaddr, filename);
}

/* wasi-libc's working folder, which starts as "/" and which chdir replaces with a copy it
 * allocates, freeing the one before once it has allocated one. A call that sets no PWD must
 * find "/", as perl.wasm does, not the making's folder; chdir("/") cannot set it, since no
 * grant is at /. The making's copy was allocated, so a new one is, for wasi-libc's next chdir
 * to free. */
extern char *__wasilibc_cwd;
static void forget_cwd(void)
{
    if (strcmp(__wasilibc_cwd, "/") != 0) {
        char *root = strdup("/");
        if (root == NULL)
            abort();
        free(__wasilibc_cwd);
        __wasilibc_cwd = root;
    }
}

/* The environment's strings as WASI gave them, which wasi-libc keeps in one block it never frees,
 * and those of the environment as the prelude left it. Each is zeroed before the environment is
 * dropped: freed is not erased, and the image would otherwise hold every value twice, once in
 * %ENV and once here. */
static char **environment_at_entry(void)
{
    size_t n = 0;
    if (environ == NULL || environ == (char **)-1)
        return NULL;
    while (environ[n] != NULL)
        n++;
    char **copy = (char **)calloc(n + 1, sizeof(char *));
    if (copy == NULL)
        abort();
    memcpy(copy, environ, n * sizeof(char *));
    return copy;
}

static void zero_strings(char **list)
{
    if (list == NULL || list == (char **)-1)
        return;
    for (char **e = list; *e != NULL; e++)
        memset(*e, 0, strlen(*e));
}

static int prepare_called, prepared, run_called;
static int open_files = -1;
/* C's errno as perl_construct left it. A croak while perl_parse reads the switches ends with
 * errno as its status, as Perl's die does; the second parse starts from it again, not from
 * whatever run's own preamble left. */
static int errno_after_construct;

__attribute__((export_name("wasi_perl_open_files"))) int32_t wasi_perl_open_files(void);
__attribute__((export_name("wasi_perl_open_files"))) int32_t wasi_perl_open_files(void)
{
    return open_files;
}

__attribute__((export_name("prepare"))) int32_t wasi_perl_prepare(void);
__attribute__((export_name("prepare"))) int32_t wasi_perl_prepare(void)
{
    if (prepare_called || run_called)
        abort();
    prepare_called = 1;
    const uintptr_t sp_at_entry = stack_pointer();
    int argc;
    char **argv = wasi_argv(&argc); /* kept: PL_origargv points into it until run's parse */
    char **given_environment = environment_at_entry();

    clocks_pinned = 1;
    PERL_SYS_INIT3(&argc, &argv, &environ);
    PERL_SYS_FPU_INIT;
    my_perl = perl_alloc();
    if (my_perl == NULL)
        abort();
    perl_construct(my_perl); /* the hash seed: Perl_seed reads /dev/urandom, so random_get */
    PL_perl_destruct_level = 0;
    PL_exit_flags |= PERL_EXIT_DESTRUCT_END; /* the prelude's END blocks end every call */
    errno_after_construct = errno;
    int status = perl_parse(my_perl, xs_init, argc, argv, NULL);
    if (status == 0)
        status = perl_run(my_perl);
    /* Before any early return, so that what the prelude wrote reaches the host. */
    clocks_pinned = 0;
    PerlIO_flush(NULL);
    (fflush)(NULL); /* C's stdio too; Perl's nostdio.h hides the name behind a macro */
    if (status != 0)
        return status;

    open_files = wasi_perl_fd_table_open_files();
    wasi_perl_fd_table_reset();
    __wasilibc_reset_preopens();
    zero_strings(environ);
    zero_strings(given_environment);
    free(given_environment);
    __wasilibc_deinitialize_environ();
    forget_cwd();
    if (stack_pointer() != sp_at_entry)
        abort();
    prepared = 1;
    return 0;
}

/* perl_construct's seeding, again, from this instance: srand's override from PERL_RAND_SEED,
 * the internal random state from PERL_INTERNAL_RAND_SEED or Perl_seed, and the hash-order bits.
 * The hash seed and the hash function's state are the image's: every key in it was hashed
 * with them. */
static void reseed(void)
{
    UV n = 0;
    const char *e;
    PL_srand_called = FALSE;
    if ((e = PerlEnv_getenv("PERL_RAND_SEED")) &&
        grok_number(e, strlen(e), &n) == IS_NUMBER_IN_UV) {
        PL_srand_override_next = n;
        PERL_SRAND_OVERRIDE_NEXT_INIT();
    }
    else {
        PL_srand_override = 0;
    }
    if (!(e = PerlEnv_getenv("PERL_INTERNAL_RAND_SEED")) ||
        grok_number(e, strlen(e), &n) != IS_NUMBER_IN_UV)
        n = Perl_seed(aTHX);
    Perl_drand48_init_r(&PL_internal_random_state, (U32)n);
    if (PL_hash_rand_bits_enabled == 1)
        PL_hash_rand_bits = Perl_seed(aTHX);
    if (!PL_hash_rand_bits)
        PL_hash_rand_bits = 0x8110ba9d;
}

/* The special variables of perlvar with a built-in meaning that perl.wasm starts with a fixed
 * value and that the second parse does not set (it sets $@ $! $? $/ $" $0 @ARGV $^W $^T $^X and
 * the selected handle itself), each put back as a fresh interpreter has it, so a call from the
 * image behaves as a call from perl.wasm. Package variables with no built-in meaning stay as
 * the prelude left them: that is the preload. */
static void undef_gv_sv(GV *gv)
{
    if (gv != NULL && isGV_with_GP(gv) && GvSV(gv) != NULL)
        sv_set_undef(GvSV(gv));
}

static GV *existing_gv(const char *name)
{
    return gv_fetchpv(name, GV_NOTQUAL, SVt_PV); /* never made here: a missing one is fresh */
}

static void fresh_handle(GV *gv)
{
    IO *io = gv != NULL && isGV_with_GP(gv) ? GvIOp(gv) : NULL;
    if (io == NULL)
        return;
    IoLINES(io) = 0;       /* $. */
    IoPAGE(io) = 0;        /* $% */
    IoPAGE_LEN(io) = 60;   /* $=, as newIO makes it */
    IoLINES_LEFT(io) = 0;  /* $- */
    Safefree(IoTOP_NAME(io)); /* $^ */
    IoTOP_NAME(io) = NULL;
    IoTOP_GV(io) = NULL;
    Safefree(IoFMT_NAME(io)); /* $~ */
    IoFMT_NAME(io) = NULL;
    IoFMT_GV(io) = NULL;
    Safefree(IoBOTTOM_NAME(io));
    IoBOTTOM_NAME(io) = NULL;
    IoBOTTOM_GV(io) = NULL;
    IoFLAGS(io) = 0; /* $| among them */
}

static void fresh_special_variables(void)
{
    GV *gv;
    undef_gv_sv(PL_defgv); /* $_ */
    if (GvAV(PL_defgv) != NULL)
        av_clear(GvAV(PL_defgv)); /* main's @_ */
    undef_gv_sv(PL_ofsgv);      /* $, */
    SvREFCNT_dec(PL_ors_sv);    /* $\ */
    PL_ors_sv = NULL;
    if ((gv = existing_gv(";")) != NULL && GvSV(gv) != NULL)
        sv_setpvs(GvSV(gv), "\034");
    Safefree(PL_inplace); /* $^I */
    PL_inplace = NULL;
    undef_gv_sv(PL_replgv); /* $^R */
    if (PL_bodytarget != NULL)
        sv_setpvs(PL_bodytarget, ""); /* $^A */
    if ((gv = existing_gv("\f")) != NULL && GvSV(gv) != NULL)
        sv_setpvs(GvSV(gv), "\f"); /* $^L */
    PL_chopset = " \n-";         /* $: */
    if ((gv = existing_gv(":")) != NULL && GvSV(gv) != NULL)
        sv_setpv(GvSV(gv), PL_chopset);
    PL_maxsysfd = MAXSYSFD; /* $^F */
    PL_perldb = 0;          /* $^P */
    PL_minus_c = FALSE;     /* $^C */
    PL_debug = 0;           /* $^D */
    PL_utf8cache = 1;       /* ${^UTF8CACHE} */
    /* The regular-expression engine's tunables, which it sets to their defaults when it finds
     * them unset, as a fresh interpreter has them. */
    undef_gv_sv(existing_gv("\022E_TRIE_MAXBUF"));
    undef_gv_sv(existing_gv("\022E_COMPILE_RECURSION_LIMIT"));
    undef_gv_sv(existing_gv(PERL_VAR_MAX_NESTED_EVAL_BEGIN_BLOCKS));
    PL_last_in_gv = NULL; /* $. and ${^LAST_FH} */
    PL_curpm = NULL;      /* $&, $1, ${^LAST_SUCCESSFUL_PATTERN} */
    PL_curpm_under = NULL;
    if (PL_argvgv != NULL) {
        IO *io = GvIOp(PL_argvgv);
        undef_gv_sv(PL_argvgv); /* $ARGV */
        if (io != NULL) {
            /* The prelude can have read <> only from its standard input: a file it left open
             * fails the making. That handle is the image's STDIN, kept apart from ARGV. */
            if (IoIFP(io) == PerlIO_stdin())
                IoIFP(io) = IoOFP(io) = NULL;
            IoLINES(io) = 0;
            IoFLAGS(io) = IOf_ARGV | IOf_START;
        }
    }
    fresh_handle(PL_stdingv);
    fresh_handle(existing_gv("STDOUT"));
    fresh_handle(PL_stderrgv);
}

/* %ENV emptied, so the parse fills it from this instance's environment alone: it stores each
 * variable into the hash it finds, and the image's still holds the making's. The hash's magic
 * would clear the C environment with it, so it is taken off first; the parse puts it back. */
static void fresh_environment_hash(void)
{
    HV *hv = PL_envgv != NULL && isGV_with_GP(PL_envgv) ? GvHV(PL_envgv) : NULL;
    if (hv == NULL)
        return;
    sv_unmagic(MUTABLE_SV(hv), PERL_MAGIC_env);
    hv_clear(hv);
}

__attribute__((export_name("run"))) void wasi_perl_run(void);
__attribute__((export_name("run"))) void wasi_perl_run(void)
{
    if (!prepared || run_called)
        abort();
    run_called = 1;
    __wasilibc_initialize_environ();
    {
        const char *pwd = getenv("PWD"); /* as wasi-stubs.c's constructor does for perl.wasm */
        if (pwd && pwd[0] == '/')
            (void)chdir(pwd);
    }
    int argc;
    char **argv = wasi_argv(&argc);
    reseed();
    fresh_environment_hash();
    fresh_special_variables();
    av_clear(GvAVn(PL_incgv)); /* filled again by the parse, as perl.wasm fills it */
    if (PL_e_script) {
        SvREFCNT_dec(PL_e_script);
        PL_e_script = NULL;
    }
    PerlIO_clearerr(PerlIO_stdin());
    PerlIO_clearerr(PerlIO_stdout());
    PerlIO_clearerr(PerlIO_stderr());
    kept_stash = PL_defstash;
    SvREFCNT_inc_simple_void_NN(kept_stash);
    kept_isarev = PL_isarev;
    SvREFCNT_inc_simple_void_NN(kept_isarev);
    second_parse = 1;
    /* perl.wasm opens its three standard PerlIO handles at their first use, inside the parse,
     * and the buffer layer then asks each descriptor whether it is a terminal and where it
     * stands. The image's handles were opened at the making; the same two questions are asked
     * here, of this instance's descriptors, so errno is as perl.wasm's would be. */
    errno = errno_after_construct;
    for (int fd = 0; fd <= 2; fd++) {
        (void)isatty(fd);
        (void)lseek(fd, 0, SEEK_CUR);
    }
    int status = perl_parse(my_perl, NULL, argc, argv, NULL);
    /* A parse that succeeded passed both steps the wrappers keep; one that ended early (a bad
     * switch, -v) may not have, and destructs anyway. */
    if (status == 0 && (kept_stash != NULL || kept_isarev != NULL))
        abort();
    if (status == 0)
        perl_run(my_perl);
    status = perl_destruct(my_perl);
    perl_free(my_perl);
    PERL_SYS_TERM();
    exit(status);
}
