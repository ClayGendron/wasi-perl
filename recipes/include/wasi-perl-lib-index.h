/* The library index: every file and folder under the library the runner grants read-only at
 * /usr/local/lib/perl5, by its path relative to that folder, compiled into the module.
 *
 * scripts/lib_index.sh writes the index of a built library as one C source, and the build links
 * it into perl.wasm. wasi-lib-index-empty.c is the index of no library, which every other
 * program wasi-cc links gets. The descriptor table (wasi-fd-table.c) reads it. */
#ifndef WASI_PERL_LIB_INDEX_H
#define WASI_PERL_LIB_INDEX_H

#include <stdint.h>

struct wasi_perl_lib_index {
    /* The number of names. 0 means no index: every lookup is the host's. */
    uint32_t count;
    /* The names, sorted in byte order, the order strcmp gives. No name is empty, starts or ends
     * with '/', or holds an empty, "." or ".." component. A name is a folder exactly when another
     * name starts with it and a '/'. */
    const char *const *names;
    /* "wasi-perl-lib-index-sha256:" and the SHA-256 of the names, one per line, in this order.
     * The structure holds it, so the module holds it whenever it holds the index. */
    const char *marker;
};

extern const struct wasi_perl_lib_index wasi_perl_lib_index;

#endif
