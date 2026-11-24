/* Research prototype: the guest's descriptor table (recipes/wasi-fd-table.c) with one more
 * function, which empties it so that the next WASI call fills it again from the host's open
 * descriptors. A snapshot made after the table filled would otherwise carry the snapshot host's
 * descriptors and grants into every later instance. */
#include "../../../recipes/wasi-fd-table.c"

void wasi_perl_fd_table_reset(void)
{
    memset(files, 0, sizeof files);
    memset(slot, 0, sizeof slot);
    memset(cloexec, 0, sizeof cloexec);
    memset(grant, 0, sizeof grant);
    started = 0;
}
