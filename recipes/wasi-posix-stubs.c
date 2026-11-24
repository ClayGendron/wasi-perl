/* Stand-ins for the signal, terminal, special-file and time zone functions the POSIX
 * extension calls and wasi-libc does not provide.
 *
 * Each has the exact prototype of its declaration in include/wasi-perl-posix.h: a call through
 * a mismatched signature traps on WebAssembly. Signal sets are plain bit sets, as wide as
 * wasi-libc's one-byte sigset_t: signals 1 to 8. Everything that would deliver, block or wait
 * for a signal is refused with ENOSYS, because WASI preview1 has no signals. The clock shims
 * give Time::HiRes integer clock ids. */
#include <errno.h>
#include <limits.h>
#include <signal.h>
#include <string.h>
#include <sys/types.h>
#include <time.h>

#include "wasi-perl-libc.h"

/* A set holds the signals its bits can: 1 to 8 with wasi-libc's one-byte sigset_t. */
#define SET_BITS (sizeof(sigset_t) * CHAR_BIT)

static int bad_signal(int sig) {
    if (sig < 1 || sig >= _NSIG || (unsigned)sig > SET_BITS) {
        errno = EINVAL;
        return 1;
    }
    return 0;
}

int sigemptyset(sigset_t *set) { memset(set, 0, sizeof *set); return 0; }

int sigfillset(sigset_t *set) {
    int sig;
    memset(set, 0, sizeof *set);
    for (sig = 1; sig < _NSIG && (unsigned)sig <= SET_BITS; sig++)
        sigaddset(set, sig);
    return 0;
}

int sigaddset(sigset_t *set, int sig) {
    unsigned char *bits = (unsigned char *)set;
    if (bad_signal(sig))
        return -1;
    bits[(sig - 1) / CHAR_BIT] |= (unsigned char)(1u << ((sig - 1) % CHAR_BIT));
    return 0;
}

int sigdelset(sigset_t *set, int sig) {
    unsigned char *bits = (unsigned char *)set;
    if (bad_signal(sig))
        return -1;
    bits[(sig - 1) / CHAR_BIT] &= (unsigned char)~(1u << ((sig - 1) % CHAR_BIT));
    return 0;
}

int sigismember(const sigset_t *set, int sig) {
    const unsigned char *bits = (const unsigned char *)set;
    /* No signal the set cannot hold is a member. POSIX's -1 would read as true in Perl. */
    if (bad_signal(sig))
        return 0;
    return (bits[(sig - 1) / CHAR_BIT] >> ((sig - 1) % CHAR_BIT)) & 1;
}

int sigaction(int sig, const struct sigaction *act, struct sigaction *oact) {
    (void)sig; (void)act; (void)oact;
    errno = ENOSYS;
    return -1;
}

int sigprocmask(int how, const sigset_t *set, sigset_t *oset) {
    (void)how; (void)set; (void)oset;
    errno = ENOSYS;
    return -1;
}

int sigpending(sigset_t *set) { (void)set; errno = ENOSYS; return -1; }
int sigsuspend(const sigset_t *mask) { (void)mask; errno = ENOSYS; return -1; }
int pause(void) { errno = ENOSYS; return -1; }

int mknod(const char *path, mode_t mode, dev_t dev) {
    (void)path; (void)mode; (void)dev;
    errno = ENOSYS;
    return -1;
}

char *ttyname(int fd) { (void)fd; errno = ENOTTY; return NULL; }

void tzset(void) {}

/* Time since the program began, which is what wasi-libc's emulated clock() measures. */
static int cpu_time(struct timespec *ts) {
    clock_t c = clock();
    if (c == (clock_t)-1) {
        errno = EINVAL;
        return -1;
    }
    ts->tv_sec = (time_t)(c / CLOCKS_PER_SEC);
    ts->tv_nsec = (long)(c % CLOCKS_PER_SEC) * (1000000000L / CLOCKS_PER_SEC);
    return 0;
}

int wasi_perl_clock_gettime(wasi_perl_clockid_t id, struct timespec *ts) {
    switch (id) {
    case WASI_PERL_CLOCK_REALTIME: return clock_gettime(CLOCK_REALTIME, ts);
    case WASI_PERL_CLOCK_MONOTONIC: return clock_gettime(CLOCK_MONOTONIC, ts);
    case WASI_PERL_CLOCK_PROCESS_CPUTIME_ID:
    case WASI_PERL_CLOCK_THREAD_CPUTIME_ID: return cpu_time(ts);
    default: errno = EINVAL; return -1;
    }
}

int wasi_perl_clock_getres(wasi_perl_clockid_t id, struct timespec *ts) {
    switch (id) {
    case WASI_PERL_CLOCK_REALTIME: return clock_getres(CLOCK_REALTIME, ts);
    case WASI_PERL_CLOCK_MONOTONIC:
    case WASI_PERL_CLOCK_PROCESS_CPUTIME_ID:
    case WASI_PERL_CLOCK_THREAD_CPUTIME_ID: return clock_getres(CLOCK_MONOTONIC, ts);
    default: errno = EINVAL; return -1;
    }
}

/* Returns the error number, as clock_nanosleep does; only the two real clocks can be slept on. */
int wasi_perl_clock_nanosleep(wasi_perl_clockid_t id, int flags, const struct timespec *req,
                              struct timespec *rem) {
    switch (id) {
    case WASI_PERL_CLOCK_REALTIME: return clock_nanosleep(CLOCK_REALTIME, flags, req, rem);
    case WASI_PERL_CLOCK_MONOTONIC: return clock_nanosleep(CLOCK_MONOTONIC, flags, req, rem);
    default: return EINVAL;
    }
}
