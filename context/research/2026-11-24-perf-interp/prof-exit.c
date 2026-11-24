/* A profiling-only stand-in, never shipped: wasmtime's CLI writes a guest profile only when
 * _start returns, and perl always leaves through exit(), which ends in proc_exit. Here exit()
 * jumps back into main, which returns the status, so _start returns and the profile is written.
 * atexit handlers are skipped; perl has flushed its PerlIO handles in perl_destruct by then. */
#include <setjmp.h>

static jmp_buf back;
static int armed, status;

int __real___main_argc_argv(int argc, char **argv);
_Noreturn void __real_exit(int s);

int __wrap___main_argc_argv(int argc, char **argv)
{
    if (setjmp(back))
        return status;
    armed = 1;
    return __real___main_argc_argv(argc, argv);
}

_Noreturn void __wrap_exit(int s)
{
    if (!armed)
        __real_exit(s);
    armed = 0;
    status = s;
    longjmp(back, 1);
}
