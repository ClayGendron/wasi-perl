/* Declarations the POSIX, Time::Piece, Socket and Time::HiRes extensions need and wasi-libc
 * leaves out or gives a shape Perl cannot use,
 * included through wasi-perl-libc.h ahead of every translation unit. Each function is
 * defined in wasi-posix-stubs.c with exactly this prototype.
 *
 * WASI preview1 has no signals, no terminals, no special files and no time zone tables.
 * The core never uses these (config.sh leaves HAS_SIGACTION, HAS_SIGPROCMASK and the rest
 * undefined); the POSIX extension calls them unconditionally on a Unix-like system. */
#ifndef WASI_PERL_POSIX_H
#define WASI_PERL_POSIX_H

#include <signal.h>
#include <sys/types.h>
#include <time.h>

#ifdef __cplusplus
extern "C" {
#endif

/* wasi-libc's sigset_t is one byte, so a set holds signals 1 to 8 only: adding or removing a
 * larger number fails with EINVAL, and no such signal is a member. Nothing can be delivered,
 * blocked or waited for. */
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

/* Clock ids as integers with Linux's values. wasi-libc's clockid_t is a pointer to one of two
 * clock objects, so an integer id from Perl, such as clock_gettime(1) or a mistyped -1, would
 * be read as an address. These take an integer, map the ids they know to wasi-libc's clocks
 * and fail any other with EINVAL. The process and thread CPU clocks are the emulation
 * wasi-libc offers for clock(): time since the program began. */
typedef int wasi_perl_clockid_t;
#define WASI_PERL_CLOCK_REALTIME 0
#define WASI_PERL_CLOCK_MONOTONIC 1
#define WASI_PERL_CLOCK_PROCESS_CPUTIME_ID 2
#define WASI_PERL_CLOCK_THREAD_CPUTIME_ID 3
int wasi_perl_clock_gettime(wasi_perl_clockid_t id, struct timespec *ts);
int wasi_perl_clock_getres(wasi_perl_clockid_t id, struct timespec *ts);
int wasi_perl_clock_nanosleep(wasi_perl_clockid_t id, int flags, const struct timespec *req,
                              struct timespec *rem);

#ifdef __cplusplus
}
#endif

/* Time::HiRes takes clock ids from Perl as integers (its typemap is T_IV). Its own define,
 * set on its compile line ahead of this header, gives it the integer ids and the shims. */
#ifdef TIME_HIRES_CLOCK_GETTIME
#undef CLOCK_REALTIME
#undef CLOCK_MONOTONIC
#define CLOCK_REALTIME WASI_PERL_CLOCK_REALTIME
#define CLOCK_MONOTONIC WASI_PERL_CLOCK_MONOTONIC
#define CLOCK_PROCESS_CPUTIME_ID WASI_PERL_CLOCK_PROCESS_CPUTIME_ID
#define CLOCK_THREAD_CPUTIME_ID WASI_PERL_CLOCK_THREAD_CPUTIME_ID
#define clockid_t wasi_perl_clockid_t
#define clock_gettime wasi_perl_clock_gettime
#define clock_getres wasi_perl_clock_getres
#define clock_nanosleep wasi_perl_clock_nanosleep
#endif

#endif
