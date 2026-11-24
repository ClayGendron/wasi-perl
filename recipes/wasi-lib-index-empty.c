/* The index of no library: a count of 0, so the descriptor table asks the host about every
 * path, as it does without an index. The build puts it in the descriptor table's archive, which
 * wasi-cc links into every program, so every link gets it unless an index object named before
 * the archive defines the index first: the linker takes an archive member only for a symbol
 * still undefined. The marker's hash is that of no names, the SHA-256 of nothing. */
#include <stddef.h>

#include "wasi-perl-lib-index.h"

const struct wasi_perl_lib_index wasi_perl_lib_index = {
    0,
    NULL,
    "wasi-perl-lib-index-sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
};
