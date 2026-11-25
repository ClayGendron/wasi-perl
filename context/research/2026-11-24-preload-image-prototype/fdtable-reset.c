/* Research prototype, not shipped: the guest's descriptor table (recipes/wasi-fd-table.c) with
 * two more functions, which a build would add to that file itself.
 *
 *   wasi_perl_fd_table_open_files  the descriptors open now that are neither stdio nor a grant.
 *   wasi_perl_fd_table_reset       the table emptied, the grants of /dev and of the library
 *                                  forgotten, so the next WASI call fills it again from the
 *                                  descriptors of the instance it runs in. */
#include "../../../recipes/wasi-fd-table.c"

int wasi_perl_fd_table_open_files(void)
{
    int n = 0;
    for (int v = 3; v < FD_LIMIT; v++)
        if (slot[v] != 0 && !grant[v])
            n++;
    return n;
}

void wasi_perl_fd_table_reset(void)
{
    memset(files, 0, sizeof files);
    memset(slot, 0, sizeof slot);
    memset(cloexec, 0, sizeof cloexec);
    memset(grant, 0, sizeof grant);
    devdir = -1;
    libdir = -1;
    started = 0;
}
