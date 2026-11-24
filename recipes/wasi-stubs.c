/* Stand-ins for the C library functions Perl calls that wasi-libc does not provide.
 *
 * Each has the exact prototype of the declaration Perl sees (include/pwd.h, include/grp.h,
 * include/wasi-perl-libc.h): a call through a mismatched signature traps on WebAssembly.
 * A refusal sets errno to ENOSYS and returns failure; a lookup finds nothing. */
#include <errno.h>
#include <grp.h>
#include <pwd.h>
#include <stdlib.h>
#include <sys/types.h>
#include <unistd.h>

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

/* No processes and no signals between them. */
int kill(pid_t pid, int sig) { (void)pid; (void)sig; errno = ENOSYS; return -1; }
/* There is no file creation mask to change; report the conventional one. */
mode_t umask(mode_t mask) { (void)mask; return 022; }
int execv(const char *path, char *const argv[]) { (void)path; (void)argv; errno = ENOSYS; return -1; }
int execvp(const char *file, char *const argv[]) { (void)file; (void)argv; errno = ENOSYS; return -1; }
int execl(const char *path, const char *arg, ...) { (void)path; (void)arg; errno = ENOSYS; return -1; }
pid_t wait(int *status) { (void)status; errno = ENOSYS; return -1; }
int do_aspawn(void *really, void **mark, void **sp) { (void)really; (void)mark; (void)sp; errno = ENOSYS; return -1; }
int do_spawn(char *cmd) { (void)cmd; errno = ENOSYS; return -1; }
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
