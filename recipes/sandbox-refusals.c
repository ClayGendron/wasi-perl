/* Refusals for what WASI preview1 cannot do: it has no processes and no signals.
 *
 * Each operation that would need one dies through Perl_croak with
 * "<what> is not available in the wasi-perl sandbox", so eval catches it, $@ holds it, and an
 * uncaught one ends the program with status 255 and the message on stderr, never a trap.
 *
 * No line of Perl's source is changed. The link passes -Wl,--wrap=<symbol> for each Perl
 * function named here (recipes/wasi-cc), so every call to it from another object reaches
 * __wrap_<symbol> instead, and __real_<symbol> still reaches Perl's own. Every wrapper is
 * declared with __typeof__ the function it stands in for, so a prototype that drifts from
 * Perl's fails to compile instead of trapping at run time.
 *
 * The same file makes the status of a die taken from errno the native one: WASI numbers errno
 * differently from Linux (ENOENT is 44, not 2), and the reference is native perl on Linux. */
#include "EXTERN.h"
#include "perl.h"

#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <wasi/libc.h>

#define SANDBOX " is not available in the wasi-perl sandbox"

/* Dies with the refusal. errno is cleared first: a die takes its exit status from errno, and
 * an uncaught refusal ends with 255 as any die with nothing in errno does. */
__attribute__((noreturn)) static void refuse(pTHX_ const char *what)
{
    errno = 0;
    Perl_croak(aTHX_ "%s" SANDBOX, what);
}

/* Operations whose pp function has no internal function to wrap: the op table names them from
 * globals.o, so the wrap catches every dispatch. */
#define REFUSE_PP(name, what)                                                                    \
    __typeof__(Perl_pp_##name) __wrap_Perl_pp_##name;                                            \
    OP *__wrap_Perl_pp_##name(pTHX)                                                              \
    {                                                                                            \
        refuse(aTHX_ what);                                                                      \
    }

REFUSE_PP(fork, "fork")
REFUSE_PP(wait, "wait")
REFUSE_PP(waitpid, "waitpid")
REFUSE_PP(alarm, "alarm")
REFUSE_PP(pipe_op, "pipe")

/* getppid: the parent of the guest is the host, outside the sandbox. Like a process whose
 * parent is in another PID namespace on Linux, it reads 0. wasi-libc has no getppid. */
__typeof__(Perl_pp_getppid) __wrap_Perl_pp_getppid;
OP *__wrap_Perl_pp_getppid(pTHX)
{
    dSP;
    dTARGET;
    XPUSHi(0);
    PUTBACK;
    return NORMAL;
}

/* dump: CORE::dump would abort the process to leave a core image (my_unexec raises SIGABRT at
 * itself). There is no signal to raise and no core to leave. */
__typeof__(Perl_my_unexec) __wrap_Perl_my_unexec;
void __wrap_Perl_my_unexec(pTHX)
{
    refuse(aTHX_ "dump");
}

/* system: pp_system calls the platform's spawn functions when there is no fork. They are this
 * project's, so they are defined here rather than wrapped. */
int do_aspawn(void *really, void **mark, void **sp)
{
    PERL_UNUSED_ARG(really);
    PERL_UNUSED_ARG(mark);
    PERL_UNUSED_ARG(sp);
    refuse(aTHX_ "system");
}

int do_spawn(char *cmd)
{
    PERL_UNUSED_ARG(cmd);
    refuse(aTHX_ "system");
}

/* exec, in its string and list forms (pp_exec). */
__typeof__(Perl_do_exec3) __wrap_Perl_do_exec3;
bool __wrap_Perl_do_exec3(pTHX_ const char *incmd, int fd, int do_report)
{
    PERL_UNUSED_ARG(incmd);
    PERL_UNUSED_ARG(fd);
    PERL_UNUSED_ARG(do_report);
    refuse(aTHX_ "exec");
}

__typeof__(Perl_do_aexec5) __wrap_Perl_do_aexec5;
bool __wrap_Perl_do_aexec5(pTHX_ SV *really, SV **mark, SV **sp, int fd, int do_report)
{
    PERL_UNUSED_ARG(really);
    PERL_UNUSED_ARG(mark);
    PERL_UNUSED_ARG(sp);
    PERL_UNUSED_ARG(fd);
    PERL_UNUSED_ARG(do_report);
    refuse(aTHX_ "exec");
}

/* Backticks, qx// and readpipe (pp_backtick), and every piped open (doio.c), reach my_popen or
 * my_popen_list. The op being run tells them apart. */
static const char *popen_what(pTHX)
{
    return PL_op && PL_op->op_type == OP_BACKTICK ? "backticks" : "pipe open";
}

__typeof__(Perl_my_popen) __wrap_Perl_my_popen;
PerlIO *__wrap_Perl_my_popen(pTHX_ const char *cmd, const char *mode)
{
    PERL_UNUSED_ARG(cmd);
    PERL_UNUSED_ARG(mode);
    refuse(aTHX_ popen_what(aTHX));
}

__typeof__(Perl_my_popen_list) __wrap_Perl_my_popen_list;
PerlIO *__wrap_Perl_my_popen_list(pTHX_ const char *mode, int n, SV **args)
{
    PERL_UNUSED_ARG(mode);
    PERL_UNUSED_ARG(n);
    PERL_UNUSED_ARG(args);
    refuse(aTHX_ popen_what(aTHX));
}

/* kill shares pp_chown with chmod, chown, unlink and utime; apply() is given the op type. */
__typeof__(Perl_apply) __real_Perl_apply;
__typeof__(Perl_apply) __wrap_Perl_apply;
SSize_t __wrap_Perl_apply(pTHX_ I32 type, SV **mark, SV **sp)
{
    if (type == OP_KILL)
        refuse(aTHX_ "kill");
    return __real_Perl_apply(aTHX_ type, mark, sp);
}

/* Whether path names a file inside dir: dir exactly, a slash, then components none of which is
 * empty, "." or "..". So "/usr/local/lib/perl5/5.42x/A.pm" and "/usr/local/lib/perl5/5.42/../x"
 * are not inside "/usr/local/lib/perl5/5.42". */
static bool path_is_under(const char *path, const char *dir)
{
    size_t n = strlen(dir);
    const char *p;
    if (n == 0 || strncmp(path, dir, n) != 0 || path[n] != '/')
        return FALSE;
    for (p = path + n + 1;;) {
        const char *end = strchr(p, '/');
        size_t len = end ? (size_t)(end - p) : strlen(p);
        if (len == 0 || (len == 1 && p[0] == '.') || (len == 2 && p[0] == '.' && p[1] == '.'))
            return FALSE;
        if (!end)
            return TRUE;
        p = end + 1;
    }
}

/* Whether path is a file of the runtime's own library: under privlib or archlib, which the
 * embedder grants read-only, so no program can put a file there. site_perl and its arch
 * folder, inside privlib, are where an embedder's own modules would go; they do not count. */
static bool path_in_runtime_library(const char *path)
{
    if (path_is_under(path, SITELIB_EXP) || path_is_under(path, SITEARCH_EXP))
        return FALSE;
    return path_is_under(path, PRIVLIB_EXP) || path_is_under(path, ARCHLIB_EXP);
}

/* The requires of library files now running. A require opens its file (PerlIO_openn, from
 * pp_ctl.c) and hands it to lex_start just before it pushes its eval frame, at the next context
 * index of the same stack. When the file opened under OP_REQUIRE is a library file, lex_start's
 * wrapper records that frame and the path, and puts a destructor on the save stack inside the
 * frame's range, so the record goes when the frame goes, by return or by die. Neither %INC nor
 * #line, nor a string eval, a do FILE or an @INC hook (whose files require does not open
 * itself) can make such a record. */
#define LIBRARY_REQUIRES_MAX 64
static struct {
    const PERL_SI *si;
    I32 cxix;
    char *path;
} library_requires[LIBRARY_REQUIRES_MAX];
static int library_requires_n;
static PerlIO *pending_fp;
static char *pending_path;

static void drop_pending(void)
{
    Safefree(pending_path);
    pending_path = NULL;
    pending_fp = NULL;
}

static void drop_library_require(pTHX_ void *unused)
{
    PERL_UNUSED_ARG(unused);
    if (library_requires_n > 0) {
        library_requires_n--;
        Safefree(library_requires[library_requires_n].path);
        library_requires[library_requires_n].path = NULL;
    }
}

__typeof__(PerlIO_openn) __real_PerlIO_openn;
__typeof__(PerlIO_openn) __wrap_PerlIO_openn;
PerlIO *__wrap_PerlIO_openn(pTHX_ const char *layers, const char *mode, int fd, int imode,
                            int perm, PerlIO *old, int narg, SV **args)
{
    PerlIO *f = __real_PerlIO_openn(aTHX_ layers, mode, fd, imode, perm, old, narg, args);
    if (f && PL_op && PL_op->op_type == OP_REQUIRE && narg == 1 && args && args[0]) {
        const char *path = SvPV_nolen(args[0]);
        drop_pending();
        if (path_in_runtime_library(path)) {
            pending_fp = f;
            pending_path = savepv(path);
        }
    }
    return f;
}

__typeof__(Perl_lex_start) __real_Perl_lex_start;
__typeof__(Perl_lex_start) __wrap_Perl_lex_start;
void __wrap_Perl_lex_start(pTHX_ SV *line, PerlIO *rsfp, U32 flags)
{
    __real_Perl_lex_start(aTHX_ line, rsfp, flags);
    if (rsfp && rsfp == pending_fp && PL_op && PL_op->op_type == OP_REQUIRE
        && library_requires_n < LIBRARY_REQUIRES_MAX) {
        library_requires[library_requires_n].si = PL_curstackinfo;
        library_requires[library_requires_n].cxix = cxstack_ix + 1;
        library_requires[library_requires_n].path = pending_path;
        library_requires_n++;
        pending_path = NULL;
        SAVEDESTRUCTOR_X(drop_library_require, NULL);
    }
    drop_pending();
}

/* Whether the store is made while a library file's require is running, by that file's own
 * code: the innermost eval frame that is not an eval block is a recorded library require, and
 * the statement is in the file that require opened. A store made after the require has ended
 * (sigtrap's import, a library sub called later) is the program's. */
static bool in_library_require(pTHX)
{
    const char *file = PL_curcop ? CopFILE(PL_curcop) : NULL;
    const PERL_SI *si;
    if (!file)
        return FALSE;
    for (si = PL_curstackinfo; si; si = si->si_prev) {
        I32 i;
        for (i = si->si_cxix; i >= 0; i--) {
            const PERL_CONTEXT *cx = &si->si_cxstack[i];
            int r;
            if (CxTYPE(cx) != CXt_EVAL || CxTRYBLOCK(cx))
                continue;
            if (CxOLD_OP_TYPE(cx) != OP_REQUIRE)
                return FALSE;
            for (r = library_requires_n - 1; r >= 0; r--)
                if (library_requires[r].si == si && library_requires[r].cxix == i)
                    return strEQ(file, library_requires[r].path);
            return FALSE;
        }
    }
    return FALSE;
}

/* A store into %SIG. The __WARN__ and __DIE__ hooks are not signals and pass through, as do
 * names that are not signals (Perl warns about those). A store for a real signal, of any
 * value, made by the program's own code dies: a script that relies on a handler must not run on
 * believing it has one. Let through are:
 * - a delete (magic_clearsig calls this with no value): it only puts back the default;
 * - the restore at the end of a local's scope (PL_localizing is 2): it puts back the value
 *   that was there before the refused store;
 * - a store made by a library file's own code while its require runs. Core modules such as
 *   Math::Complex (local $SIG{FPE}) store a handler while they load; the handler is kept and
 *   never runs, since the sandbox delivers no signals. */
__typeof__(Perl_magic_setsig) __real_Perl_magic_setsig;
__typeof__(Perl_magic_setsig) __wrap_Perl_magic_setsig;
int __wrap_Perl_magic_setsig(pTHX_ SV *sv, MAGIC *mg)
{
    STRLEN len;
    const char *s = MgPV_const(mg, len);
    if (sv && *s != '_' && PL_localizing != 2 && whichsig_pvn(s, len) > 0
        && !in_library_require(aTHX)) {
        SV *msg = newSVpvf("signal handler for SIG%" UTF8f, UTF8fARG(0, len, s));
        sv_2mortal(msg);
        PL_localizing = 0;
        refuse(aTHX_ SvPV_nolen(msg));
    }
    return __real_Perl_magic_setsig(aTHX_ sv, mg);
}

/* WASI's errno numbers, as Linux (glibc) numbers them. ENOTCAPABLE, WASI's "the sandbox did
 * not grant this", has no Linux number; it is reported as EACCES, which is what native perl
 * would see for a file it may not reach. */
static const unsigned char linux_errno[] = {
    [E2BIG] = 7, [EACCES] = 13, [EADDRINUSE] = 98,
    [EADDRNOTAVAIL] = 99, [EAFNOSUPPORT] = 97, [EAGAIN] = 11,
    [EALREADY] = 114, [EBADF] = 9, [EBADMSG] = 74,
    [EBUSY] = 16, [ECANCELED] = 125, [ECHILD] = 10,
    [ECONNABORTED] = 103, [ECONNREFUSED] = 111, [ECONNRESET] = 104,
    [EDEADLK] = 35, [EDESTADDRREQ] = 89, [EDOM] = 33,
    [EDQUOT] = 122, [EEXIST] = 17, [EFAULT] = 14,
    [EFBIG] = 27, [EHOSTUNREACH] = 113, [EIDRM] = 43,
    [EILSEQ] = 84, [EINPROGRESS] = 115, [EINTR] = 4,
    [EINVAL] = 22, [EIO] = 5, [EISCONN] = 106,
    [EISDIR] = 21, [ELOOP] = 40, [EMFILE] = 24,
    [EMLINK] = 31, [EMSGSIZE] = 90, [EMULTIHOP] = 72,
    [ENAMETOOLONG] = 36, [ENETDOWN] = 100, [ENETRESET] = 102,
    [ENETUNREACH] = 101, [ENFILE] = 23, [ENOBUFS] = 105,
    [ENODEV] = 19, [ENOENT] = 2, [ENOEXEC] = 8,
    [ENOLCK] = 37, [ENOLINK] = 67, [ENOMEM] = 12,
    [ENOMSG] = 42, [ENOPROTOOPT] = 92, [ENOSPC] = 28,
    [ENOSYS] = 38, [ENOTCONN] = 107, [ENOTDIR] = 20,
    [ENOTEMPTY] = 39, [ENOTRECOVERABLE] = 131, [ENOTSOCK] = 88,
    [ENOTSUP] = 95, [ENOTTY] = 25, [ENXIO] = 6,
    [EOVERFLOW] = 75, [EOWNERDEAD] = 130, [EPERM] = 1,
    [EPIPE] = 32, [EPROTO] = 71, [EPROTONOSUPPORT] = 93,
    [EPROTOTYPE] = 91, [ERANGE] = 34, [EROFS] = 30,
    [ESPIPE] = 29, [ESRCH] = 3, [ESTALE] = 116,
    [ETIMEDOUT] = 110, [ETXTBSY] = 26, [EXDEV] = 18,
    [ENOTCAPABLE] = 13,
};

/* The exit of an uncaught die: Perl_my_failure_exit, as in perl.c for every platform but VMS,
 * with errno turned into its Linux number for the status. errno itself, and so $! in END
 * blocks, is left as it is. A number the table does not know is passed on unchanged. */
__typeof__(Perl_my_failure_exit) __wrap_Perl_my_failure_exit;
void __wrap_Perl_my_failure_exit(pTHX)
{
    int eno = errno;
    U32 status;
    if (eno > 0 && (size_t)eno < sizeof linux_errno && linux_errno[eno])
        eno = linux_errno[eno];
    if (eno & 255)
        status = (U32)eno;
    else {
        int exitstatus = STATUS_UNIX >> 8;
        status = (exitstatus & 255) ? (U32)exitstatus : 255;
    }
    my_exit(status);
}

/* dup2 as a move, for a descriptor that nothing else holds. WASI preview1 has no way to copy a
 * file descriptor, so wasi-libc's dup2 refuses ("Not supported"), and with it every reopen of
 * STDIN, STDOUT or STDERR onto a file: Perl opens the file at a new descriptor, dup2s it onto
 * 0, 1 or 2, and then closes the new one (S_openn_cleanup in doio.c), with no descriptor
 * opened in between. WASI's fd_renumber moves a descriptor onto another, which reaches the
 * same end state, except that the old number is closed already.
 *
 * A move is right only when the old descriptor has that one owner. The fdopen forms of open
 * (">&=", "<&=") reach the same dup2 with a descriptor another handle still uses; moving it
 * would take it from that handle. So the move happens only when PerlIO counts exactly one
 * handle on the old descriptor (the build has no threads, so the count needs no lock), and
 * anything else fails with ENOTSUP, as wasi-libc's dup2 does. Perl's other dup2 callers are
 * the child side of a fork, which the sandbox refuses before it is reached. */
static int moved_fd = -1;

int __real_dup2(int oldfd, int newfd);
int __wrap_dup2(int oldfd, int newfd);
int __wrap_dup2(int oldfd, int newfd)
{
    if (oldfd == newfd)
        return fcntl(oldfd, F_GETFD) < 0 ? -1 : newfd;
    if (oldfd < 0 || oldfd >= PL_perlio_fd_refcnt_size || PL_perlio_fd_refcnt[oldfd] != 1) {
        errno = ENOTSUP;
        return -1;
    }
    if (__wasilibc_fd_renumber(oldfd, newfd) != 0)
        return -1;
    moved_fd = oldfd;
    return newfd;
}

/* The close that follows a dup2 finds the old number gone. Natively that close succeeds, so it
 * succeeds here too, and leaves errno as it was: a die takes its exit status from errno. Every
 * other close, and any later use of the moved number, behaves as WASI's does (EBADF). */
int __real_close(int fd);
int __wrap_close(int fd);
int __wrap_close(int fd)
{
    int saved = errno;
    int r = __real_close(fd);
    if (fd >= 0 && fd == moved_fd) {
        moved_fd = -1;
        if (r < 0 && errno == EBADF) {
            errno = saved;
            return 0;
        }
    }
    return r;
}
