/* Declarations Perl's sources need and wasi-libc leaves out, included ahead of every
 * translation unit by wasi-cc. Each function here is defined in wasi-stubs.c (or, where
 * noted, sandbox-refusals.c) with exactly this prototype: on WebAssembly a call through a mismatched signature traps. */
#ifndef WASI_PERL_LIBC_H
#define WASI_PERL_LIBC_H

#include <sys/types.h>

#ifdef __cplusplus
extern "C" {
#endif

/* A single-user system: every identity is 0, and changing it is refused. */
uid_t getuid(void);
uid_t geteuid(void);
gid_t getgid(void);
gid_t getegid(void);
int setuid(uid_t uid);
int setgid(gid_t gid);

/* No processes: these are refused with ENOSYS. */
int kill(pid_t pid, int sig);
mode_t umask(mode_t mask);
int execv(const char *path, char *const argv[]);
int execvp(const char *file, char *const argv[]);
int execl(const char *path, const char *arg, ...);
pid_t wait(int *status);

/* Perl calls these for system() when the platform has no fork(); sandbox-refusals.c defines
 * them, and they refuse. */
int do_aspawn(void *really, void **mark, void **sp);
int do_spawn(char *cmd);

/* Perl opens a pipe through this name for piped opens and backticks; without pipe() in
 * config.sh it is left undefined, so it is supplied here and refuses. */
int PerlProc_pipe_cloexec(int *pipefd);

#ifdef __cplusplus
}
#endif

#endif
