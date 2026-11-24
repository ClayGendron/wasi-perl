/* Declarations the POSIX, Time::Piece and Socket extensions need and wasi-libc leaves out,
 * included through wasi-perl-libc.h ahead of every translation unit. Each function is defined in
 * wasi-posix-stubs.c with exactly this prototype.
 *
 * WASI preview1 has no signals, no terminals, no special files and no time zone tables.
 * The core never uses these (config.sh leaves HAS_SIGACTION, HAS_SIGPROCMASK and the rest
 * undefined); the POSIX extension calls them unconditionally on a Unix-like system. */
#ifndef WASI_PERL_POSIX_H
#define WASI_PERL_POSIX_H

#include <signal.h>
#include <sys/types.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Signal sets are plain data and work; nothing can be delivered, blocked or waited for. */
#ifndef SIG_BLOCK
#define SIG_BLOCK 0
#define SIG_UNBLOCK 1
#define SIG_SETMASK 2
#endif

struct sigaction {
    void (*sa_handler)(int);
    sigset_t sa_mask;
    int sa_flags;
};

int sigemptyset(sigset_t *set);
int sigfillset(sigset_t *set);
int sigaddset(sigset_t *set, int sig);
int sigdelset(sigset_t *set, int sig);
int sigismember(const sigset_t *set, int sig);

/* Refused with ENOSYS. */
int sigaction(int sig, const struct sigaction *act, struct sigaction *oact);
int sigprocmask(int how, const sigset_t *set, sigset_t *oset);
int sigpending(sigset_t *set);
int sigsuspend(const sigset_t *mask);
int pause(void);
int mknod(const char *path, mode_t mode, dev_t dev);

/* No terminals: no descriptor is one. */
char *ttyname(int fd);

/* Local time is UTC: there are no zone tables to load. */
void tzset(void);

/* There are no sockets (config.sh leaves HAS_SOCKET undefined, so every socket call dies with
 * "Unsupported socket function"), but IO::Socket::INET names SOCK_RAW when it loads, and the
 * Socket extension can only export what the headers define. wasi-libc defines SOCK_STREAM and
 * SOCK_DGRAM; SOCK_RAW takes Linux's value, which neither of those uses. */
#ifndef SOCK_RAW
#define SOCK_RAW 3
#endif

#ifdef __cplusplus
}
#endif

#endif
