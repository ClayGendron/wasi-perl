/* The user database for wasm32-wasip1, which has none.
 *
 * wasi-libc ships no <pwd.h>, but Perl's unixish.h turns on HAS_PASSWD on every Unix-like
 * system, so the structure and the functions must be declared. They are defined in
 * wasi-stubs.c to find nothing, as on a system that does not know the user. */
#ifndef WASI_PERL_PWD_H
#define WASI_PERL_PWD_H

#include <sys/types.h>

#ifdef __cplusplus
extern "C" {
#endif

struct passwd {
    char *pw_name;
    char *pw_passwd;
    uid_t pw_uid;
    gid_t pw_gid;
    char *pw_gecos;
    char *pw_dir;
    char *pw_shell;
};

struct passwd *getpwnam(const char *name);
struct passwd *getpwuid(uid_t uid);
struct passwd *getpwent(void);
void setpwent(void);
void endpwent(void);

#ifdef __cplusplus
}
#endif

#endif
