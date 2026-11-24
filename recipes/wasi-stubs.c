/* Stand-ins for the C library functions Perl calls that wasi-libc does not provide.
 *
 * Each has the exact prototype of the declaration Perl sees (include/pwd.h, include/grp.h,
 * include/wasi-perl-libc.h): a call through a mismatched signature traps on WebAssembly.
 * A refusal sets errno to ENOSYS and returns failure; a lookup finds nothing. */
#include <errno.h>
#include <grp.h>
#include <pwd.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/time.h>
#include <sys/times.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>
#include <wasi/api.h>

#include "wasi-perl-libc.h"

/* The user and group databases are empty, as on a system that does not know the user. */
struct passwd *getpwnam(const char *name) { (void)name; errno = ENOENT; return NULL; }
struct passwd *getpwuid(uid_t uid) { (void)uid; errno = ENOENT; return NULL; }
struct passwd *getpwent(void) { return NULL; }
void setpwent(void) {}
void endpwent(void) {}
struct group *getgrnam(const char *name) { (void)name; errno = ENOENT; return NULL; }
struct group *getgrgid(gid_t gid) { (void)gid; errno = ENOENT; return NULL; }
struct group *getgrent(void) { return NULL; }
void setgrent(void) {}
void endgrent(void) {}

/* A single-user system. */
uid_t getuid(void) { return 0; }
uid_t geteuid(void) { return 0; }
gid_t getgid(void) { return 0; }
gid_t getegid(void) { return 0; }
int setuid(uid_t uid) { (void)uid; errno = ENOSYS; return -1; }
int setgid(gid_t gid) { (void)gid; errno = ENOSYS; return -1; }

/* No processes and no signals between them. What a Perl program can reach of these is refused
 * by sandbox-refusals.c before it gets here; these keep the link whole. */
int kill(pid_t pid, int sig) { (void)pid; (void)sig; errno = ENOSYS; return -1; }
/* There is no file creation mask to change; report the conventional one. */
mode_t umask(mode_t mask) { (void)mask; return 022; }
int execv(const char *path, char *const argv[]) { (void)path; (void)argv; errno = ENOSYS; return -1; }
int execvp(const char *file, char *const argv[]) { (void)file; (void)argv; errno = ENOSYS; return -1; }
int execl(const char *path, const char *arg, ...) { (void)path; (void)arg; errno = ENOSYS; return -1; }
pid_t wait(int *status) { (void)status; errno = ENOSYS; return -1; }
int PerlProc_pipe_cloexec(int *pipefd) { (void)pipefd; errno = ENOSYS; return -1; }

/* An embedder gives the program a working directory by setting PWD to an absolute path:
 * WASI preview1 has no current directory of its own, so wasi-libc starts at "/" and resolves
 * relative names against it. This runs before main(). */
__attribute__((constructor)) static void wasi_perl_chdir_to_pwd(void)
{
    const char *pwd = getenv("PWD");
    if (pwd && pwd[0] == '/')
        (void)chdir(pwd);
}

/* The exit status, kept where the embedder can read it.
 *
 * wasmtime refuses a WASI exit status of 126 or more and reports an error in place of the
 * exit, yet Perl ends an uncaught die with 255 and exit() takes any byte. Every exit passes
 * through wasi-libc's __wasi_proc_exit; the link wraps it (-Wl,--wrap=__wasi_proc_exit), so
 * the status is stored here first and then passed on. The module exports
 * wasi_perl_exit_status, which returns that status, or -1 if the program has not exited: an
 * embedder calls it after a refused exit to learn the real code. An export adds no import. */
static int32_t wasi_perl_status = -1;

_Noreturn void __real___wasi_proc_exit(__wasi_exitcode_t rval);

_Noreturn void __wrap___wasi_proc_exit(__wasi_exitcode_t rval)
{
    /* A POSIX exit status is the low eight bits of what exit() was given: exit(256) ends with
     * 0 and exit(-1) with 255, natively. Masked, the stored status is 0..255, and -1 is left
     * to mean that the program never exited. */
    rval &= 0xff;
    wasi_perl_status = (int32_t)rval;
    __real___wasi_proc_exit(rval);
}

__attribute__((export_name("wasi_perl_exit_status"))) int32_t wasi_perl_exit_status(void)
{
    return wasi_perl_status;
}

/* times() in the clock tick sysconf reports.
 *
 * POSIX counts the clock_t values of times() in sysconf(_SC_CLK_TCK) ticks, and Perl divides
 * them by that number to give seconds. wasi-libc's emulated times() counts nanoseconds
 * (CLOCKS_PER_SEC) while its sysconf(_SC_CLK_TCK) answers 100, so Perl's times was ten million
 * times too large. The link wraps times (-Wl,--wrap=times) and this converts every field, and
 * the elapsed time it returns, to the ticks sysconf names, so the two agree for every caller. */
clock_t __real_times(struct tms *buf);

clock_t __wrap_times(struct tms *buf)
{
    clock_t elapsed = __real_times(buf);
    long tick = sysconf(_SC_CLK_TCK);
    clock_t per = (tick > 0 && tick <= CLOCKS_PER_SEC) ? CLOCKS_PER_SEC / tick : 1;
    if (buf) {
        buf->tms_utime /= per;
        buf->tms_stime /= per;
        buf->tms_cutime /= per;
        buf->tms_cstime /= per;
    }
    return elapsed == (clock_t)-1 ? elapsed : elapsed / per;
}

/* select() with Perl's bit vectors.
 *
 * Perl's four-argument select hands the C select() its bit vectors as they are, as fd_set
 * pointers: on POSIX systems an fd_set is that bit mask. wasi-libc's fd_set is a count and an
 * array of descriptors instead, so it read Perl's first mask bytes as a count and the bytes
 * after as descriptors: a result that hung on memory past the mask, and a trap for a mask of
 * "\xff". The link wraps select (-Wl,--wrap=select); this reads each given set as the bit mask
 * of nfds bits it is, calls wasi-libc's select with its own fd_sets, and writes the result
 * back as bits. Perl's other calls to select pass no sets, which pass through unchanged. */
int __real_select(int nfds, fd_set *readfds, fd_set *writefds, fd_set *exceptfds,
                  struct timeval *timeout);

static void bits_to_set(int nfds, const unsigned char *bits, fd_set *set)
{
    FD_ZERO(set);
    for (int fd = 0; fd < nfds; fd++)
        if (bits[fd / 8] & (1u << (fd % 8)))
            FD_SET(fd, set);
}

static void set_to_bits(int nfds, const fd_set *set, unsigned char *bits)
{
    memset(bits, 0, (size_t)(nfds + 7) / 8);
    for (size_t i = 0; i < set->__nfds; i++) {
        int fd = set->__fds[i];
        if (fd >= 0 && fd < nfds)
            bits[fd / 8] |= (unsigned char)(1u << (fd % 8));
    }
}

int __wrap_select(int nfds, fd_set *readfds, fd_set *writefds, fd_set *exceptfds,
                  struct timeval *timeout)
{
    fd_set *given[3] = {readfds, writefds, exceptfds};
    fd_set sets[3], *pass[3];
    if (nfds < 0 || nfds > FD_SETSIZE) {
        errno = EINVAL;
        return -1;
    }
    for (int i = 0; i < 3; i++) {
        pass[i] = NULL;
        if (given[i]) {
            bits_to_set(nfds, (const unsigned char *)given[i], &sets[i]);
            pass[i] = &sets[i];
        }
    }
    int found = __real_select(nfds, pass[0], pass[1], pass[2], timeout);
    if (found >= 0)
        for (int i = 0; i < 3; i++)
            if (given[i])
                set_to_bits(nfds, &sets[i], (unsigned char *)given[i]);
    return found;
}
