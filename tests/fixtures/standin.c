/* A stand-in for perl.wasm in the runner's tests: one small WASI command whose first argument
   names what it does, so each limit and each grant can be exercised without Perl. Like
   perl.wasm, it moves to $PWD before main and unwinds with setjmp/longjmp, which the compiler
   lowers to WebAssembly exception handling. */
#include <errno.h>
#include <fcntl.h>
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

__attribute__((constructor)) static void move_to_pwd(void) {
    const char *pwd = getenv("PWD");
    if (pwd && *pwd && chdir(pwd) != 0) {
        fprintf(stderr, "chdir %s: %s\n", pwd, strerror(errno));
    }
}

/* The status the guest passed to exit, or -1: wasmtime refuses proc_exit with a status of 126
   or more, so perl.wasm exports what it was given and the runner reads it (perl's uncaught die
   is 255). Linked with -Wl,--wrap=_Exit, so every exit passes through here. */
static int exit_status = -1;

#ifndef NO_EXIT_STATUS  /* built without it too, as a perl.wasm from before the export */
__attribute__((export_name("wasi_perl_exit_status"))) int wasi_perl_exit_status(void) {
    return exit_status;
}

extern _Noreturn void __real__Exit(int);

_Noreturn void __wrap__Exit(int status) {
    exit_status = status & 0xff;
    __real__Exit(status);
}
#endif

static jmp_buf where;

static void jump(int n) { longjmp(where, n); }

int main(int argc, char **argv) {
    if (argc < 2) { fputs("usage: standin MODE [ARG]\n", stderr); return 2; }
    const char *mode = argv[1];
    const char *arg = argc > 2 ? argv[2] : "";
    if (!strcmp(mode, "hello")) { puts("hello"); return 0; }
    if (!strcmp(mode, "exit")) { exit(atoi(arg)); }
    if (!strcmp(mode, "spin")) { volatile unsigned long n = 0; for (;;) n++; }
    if (!strcmp(mode, "count")) {  /* a fixed amount of work, then exit 0 */
        volatile unsigned long n = 0; unsigned long k = strtoul(arg, 0, 10);
        for (unsigned long i = 0; i < k; i++) n++;
        return 0;
    }
    if (!strcmp(mode, "print")) {  /* N bytes of 'x' to stdout, then "done" to stderr */
        unsigned long k = strtoul(arg, 0, 10); char buf[4096]; memset(buf, 'x', sizeof buf);
        while (k) { size_t n = k < sizeof buf ? k : sizeof buf; fwrite(buf, 1, n, stdout); k -= n; }
        fflush(stdout); fputs("done\n", stderr); return 0;
    }
    if (!strcmp(mode, "flood")) {  /* stdout forever */
        char buf[65536]; memset(buf, 'y', sizeof buf); for (;;) fwrite(buf, 1, sizeof buf, stdout);
    }
    if (!strcmp(mode, "alloc")) {  /* allocate and touch N bytes */
        size_t k = strtoul(arg, 0, 10); char *volatile p = malloc(k);
        if (!p) { fputs("out of memory\n", stderr); return 3; }
        memset(p, 1, k); printf("allocated %zu\n", k - 1 + p[k - 1]); return 0;
    }
    if (!strcmp(mode, "longjmp")) {
        int got = setjmp(where);
        if (got == 0) jump(7);
        printf("caught %d\n", got); return 0;
    }
    if (!strcmp(mode, "env")) {
        extern char **environ; for (char **e = environ; *e; e++) puts(*e); return 0;
    }
    if (!strcmp(mode, "cwd")) {
        char buf[1024]; if (!getcwd(buf, sizeof buf)) { perror("getcwd"); return 1; }
        puts(buf); return 0;
    }
    if (!strcmp(mode, "stdin")) {
        char buf[4096]; size_t n; while ((n = fread(buf, 1, sizeof buf, stdin)) > 0) fwrite(buf, 1, n, stdout);
        return 0;
    }
    if (!strcmp(mode, "argv")) { for (int i = 0; i < argc; i++) puts(argv[i]); return 0; }
    if (!strcmp(mode, "cat")) {
        FILE *f = fopen(arg, "rb"); if (!f) { printf("open %s: %s\n", arg, strerror(errno)); return 1; }
        char buf[4096]; size_t n; while ((n = fread(buf, 1, sizeof buf, f)) > 0) fwrite(buf, 1, n, stdout);
        fclose(f); return 0;
    }
    if (!strcmp(mode, "write")) {  /* write "written\n" to ARG */
        int fd = open(arg, O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (fd < 0) { printf("open %s: %s\n", arg, strerror(errno)); return 1; }
        if (write(fd, "written\n", 8) != 8) { printf("write: %s\n", strerror(errno)); return 1; }
        close(fd); puts("ok"); return 0;
    }
    if (!strcmp(mode, "trap")) { __builtin_trap(); }
    if (!strcmp(mode, "trap-after-exit-status")) {  /* a real trap after a status was recorded */
        exit_status = 9; __builtin_trap();
    }
    if (!strcmp(mode, "spin-after-exit-status")) { exit_status = 9; volatile unsigned long n = 0; for (;;) n++; }
    fprintf(stderr, "unknown mode %s\n", mode); return 2;
}
