/* The group database for wasm32-wasip1, which has none.
 *
 * wasi-libc ships no <grp.h>, but Perl's unixish.h turns on HAS_GROUP on every Unix-like
 * system, so the structure and the functions must be declared. They are defined in
 * wasi-stubs.c to find nothing. */
#ifndef WASI_PERL_GRP_H
#define WASI_PERL_GRP_H

#include <sys/types.h>

#ifdef __cplusplus
extern "C" {
#endif

struct group {
    char *gr_name;
    char *gr_passwd;
    gid_t gr_gid;
    char **gr_mem;
};

struct group *getgrnam(const char *name);
struct group *getgrgid(gid_t gid);
struct group *getgrent(void);
void setgrent(void);
void endgrent(void);

#ifdef __cplusplus
}
#endif

#endif
