/* Wait statuses for wasm32-wasip1, which has no child processes.
 *
 * wasi-libc ships no <sys/wait.h>, but the POSIX extension includes it on every Unix-like
 * system. Nothing here waits: wait() is a stub that fails, and waitpid() is not declared, so
 * Perl's HAS_WAITPID stays off. The W* macros only decode a status number in the layout Perl
 * uses for $? (exit code in bits 8 to 15, signal number in bits 0 to 6), so POSIX::WEXITSTATUS
 * and its kin give the same answers as on Linux. */
#ifndef WASI_PERL_SYS_WAIT_H
#define WASI_PERL_SYS_WAIT_H

#include <sys/types.h>

#define WNOHANG 1
#define WUNTRACED 2

#define WEXITSTATUS(s) (((s) >> 8) & 0xff)
#define WTERMSIG(s) ((s) & 0x7f)
#define WSTOPSIG(s) WEXITSTATUS(s)
#define WIFEXITED(s) (WTERMSIG(s) == 0)
#define WIFSTOPPED(s) (((s) & 0xff) == 0x7f)
#define WIFSIGNALED(s) (WTERMSIG(s) != 0 && WTERMSIG(s) != 0x7f)

#endif
