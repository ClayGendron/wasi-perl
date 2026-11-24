/* Stand-ins for the signal, terminal, special-file and time zone functions the POSIX
 * extension calls and wasi-libc does not provide.
 *
 * Each has the exact prototype of its declaration in include/wasi-perl-posix.h: a call through
 * a mismatched signature traps on WebAssembly. Signal sets are plain bit sets and behave as on
 * any system; everything that would deliver, block or wait for a signal is refused with
 * ENOSYS, because WASI preview1 has no signals. */
#include <errno.h>
#include <limits.h>
#include <signal.h>
#include <string.h>
#include <sys/types.h>

#include "wasi-perl-libc.h"

/* Signal numbers run from 1 to _NSIG - 1, as in the C library's own headers. */
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
    for (sig = 1; sig < _NSIG; sig++)
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
    if (bad_signal(sig))
        return -1;
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
