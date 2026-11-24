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

/* A store into %SIG. The __WARN__ and __DIE__ hooks are not signals and pass through, as do
 * names that are not signals (Perl warns about those). A store for a real signal, of any
 * value, dies: a script that relies on a handler must not run on believing it has one. The one
 * store let through is the restore at the end of a local's scope (PL_localizing is 2): it
 * puts back the value that was there before the refused store. */
__typeof__(Perl_magic_setsig) __real_Perl_magic_setsig;
__typeof__(Perl_magic_setsig) __wrap_Perl_magic_setsig;
int __wrap_Perl_magic_setsig(pTHX_ SV *sv, MAGIC *mg)
{
    STRLEN len;
    const char *s = MgPV_const(mg, len);
    if (*s != '_' && PL_localizing != 2 && whichsig_pvn(s, len) > 0) {
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
