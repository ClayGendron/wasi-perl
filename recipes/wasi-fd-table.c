/* Descriptors the program can duplicate, over WASI's, which it cannot.
 *
 * POSIX lets a process hold many numbers for one open file (dup, dup2, fcntl F_DUPFD), and Perl
 * relies on that to save and restore STDOUT and STDERR, for open(">&"), and in Test::More.
 * WASI preview1 gives one number per open file and no call to make a second. So the guest
 * numbers its own descriptors: a table maps each number the program sees (virtual) to an open
 * file, and each open file holds WASI's number for it (real) and a count of the virtual
 * numbers that refer to it. The real descriptor is closed when the last of them is.
 *
 * Every wasi-libc call that passes a descriptor to the host goes through one of the
 * __wasi_* functions of wasi-libc's bottom half, each defined in one object and called from
 * others. The link passes -Wl,--wrap=<function> for each (recipes/wasi-cc), so every call
 * reaches __wrap_<function> here, which translates the virtual number to the real one and
 * calls __real_<function>, wasi-libc's own. A call that gives back a new descriptor
 * (path_open, sock_accept) records it under the lowest free virtual number, as POSIX
 * requires of open(). fd_close and fd_renumber become operations on the table. dup, dup2,
 * dup3 and fcntl are wrapped the same way, because wasi-libc defines them to fail.
 *
 * The table fills itself on first use: every real descriptor the host opened before the
 * program started (stdio and the preopened folders, numbered from 0 with no gap, as wasi-libc
 * assumes when it looks for preopens) gets the same virtual number. Nothing runs before
 * that, so the order of constructors does not matter. The host sees the same real
 * descriptors as before. The module imports one WASI preview1 function a program without the
 * table would not: random_get, which the random device reads.
 *
 * Five more answers are the guest's own, each where the host's or wasi-libc's would differ from
 * a POSIX system's: an open of a special file is refused before the host could wait in it
 * (path_open), select reads Perl's bit vectors (select), a rename over a file the program
 * holds open succeeds where the host refuses it (path_rename), /dev/urandom and /dev/random
 * are a device of the guest's own that reads random_get (the random device), and an open with
 * no access mode reads, as on Linux (open, openat, wrapped as dup is). One more spares the
 * host a call it would answer the same: isatty of a file the look before its open found to be
 * a regular file is "not a terminal" (isatty, wrapped as dup is). Another spares the host the
 * lookups of library files that do not exist: the library index answers them (the library
 * index). */
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <unistd.h>
#include <wasi/api.h>

#include "wasi-perl-lib-index.h"

/* The most virtual numbers at once, which is also the most host descriptors one call can
 * hold: 256, macOS's default open-file limit, so that a few calls at once cannot exhaust the
 * embedding process's descriptors. The host's own descriptors, the grants among them, count
 * against it. */
#define FD_LIMIT 256

/* The real number of an open file whose host descriptor is gone: a reopen after a rename over
 * it failed. Every call through it answers EBADF, as the host would; its close succeeds. */
#define GONE ((__wasi_fd_t)-1)

/* The real number of an open file that is the guest's random device. No host descriptor stands
 * behind it, so no call may hand it to the host: each wrapper answers for it itself. */
#define VIRTUAL_RANDOM ((__wasi_fd_t)-2)

struct file {
    __wasi_fd_t real;
    uint32_t refs; /* 0: this entry is free */
    /* How path_open opened it, to open it again after a rename over it (path_rename). */
    __wasi_oflags_t oflags;
    __wasi_fdflags_t fdflags;
    __wasi_rights_t base, inheriting;
    uint8_t reopenable;
    uint8_t device; /* which random device, when real is VIRTUAL_RANDOM */
    uint8_t regular; /* the look before the open found a regular file: no terminal */
};

/* Each open file has at least one virtual number, so there are never more files than numbers.
 * slot[v] is 1 + the index in files of the open file v refers to, or 0 when v is closed. */
static struct file files[FD_LIMIT];
static uint16_t slot[FD_LIMIT];
/* FD_CLOEXEC of each virtual number. Nothing can be executed, so it only has to read back. */
static uint8_t cloexec[FD_LIMIT];
/* The grants' numbers. wasi-libc finds each preopened folder once, keeps its number, and
 * resolves every path under that folder through it for the rest of the run. So a grant's
 * number always names its grant: closing it succeeds and leaves the grant open, and nothing
 * may be put on it (dup2, dup3, fd_renumber answer EBADF). Since it is never freed, no open,
 * dup or F_DUPFD is given it, and a path under one grant can never land in another. */
static uint8_t grant[FD_LIMIT];
/* The number of the grant the guest sees at /dev, or -1 when there is none. */
static int devdir = -1;
/* The number of the grant the guest sees at /usr/local/lib/perl5, or -1 when there is none. */
static int libdir = -1;
static int started;

__typeof__(__wasi_fd_fdstat_get) __real___wasi_fd_fdstat_get;
__typeof__(__wasi_fd_prestat_get) __real___wasi_fd_prestat_get;
__typeof__(__wasi_fd_prestat_dir_name) __real___wasi_fd_prestat_dir_name;
__typeof__(__wasi_fd_filestat_get) __real___wasi_fd_filestat_get;
__typeof__(__wasi_fd_close) __real___wasi_fd_close;
__typeof__(__wasi_path_open) __real___wasi_path_open;
__typeof__(__wasi_path_rename) __real___wasi_path_rename;
static __wasi_errno_t look(__wasi_fd_t realdir, __wasi_lookupflags_t dirflags, const char *path,
                           __wasi_oflags_t oflags, uint8_t *regular);

static void bind(int v, int file, int close_on_exec)
{
    slot[v] = (uint16_t)(file + 1);
    files[file].refs++;
    cloexec[v] = (uint8_t)close_on_exec;
}

static int new_file(__wasi_fd_t real)
{
    for (int i = 0; i < FD_LIMIT; i++) {
        if (files[i].refs == 0) {
            files[i].real = real;
            files[i].reopenable = 0;
            files[i].device = 0;
            files[i].regular = 0;
            return i;
        }
    }
    abort(); /* cannot happen: there are fewer open files than numbers in use */
}

/* Whether the grant at real descriptor fd is the one the guest sees at `want`, by its name as
 * wasi-libc reads it: trailing slashes do not count. */
static int is_named(__wasi_fd_t fd, const __wasi_prestat_t *pre, const char *want)
{
    char name[32];
    size_t n = pre->u.dir.pr_name_len;
    if (n >= sizeof name ||
        __real___wasi_fd_prestat_dir_name(fd, (uint8_t *)name, (__wasi_size_t)n) != 0)
        return 0;
    while (n > 1 && name[n - 1] == '/')
        n--;
    return n == strlen(want) && memcmp(name, want, n) == 0;
}

/* The descriptors the host opened before the program started, each under its own number.
 * 0, 1 and 2 may be missing; from 3, the first number the host does not know ends the scan. */
static void start(void)
{
    started = 1;
    for (int fd = 0; fd < FD_LIMIT; fd++) {
        __wasi_fdstat_t st;
        if (__real___wasi_fd_fdstat_get((__wasi_fd_t)fd, &st) == __WASI_ERRNO_BADF) {
            if (fd > 2)
                break;
            continue;
        }
        __wasi_prestat_t pre;
        bind(fd, new_file((__wasi_fd_t)fd), fd > 2);
        grant[fd] = fd > 2 && __real___wasi_fd_prestat_get((__wasi_fd_t)fd, &pre) == 0 &&
                    pre.tag == __WASI_PREOPENTYPE_DIR;
        if (grant[fd] && devdir < 0 && is_named((__wasi_fd_t)fd, &pre, "/dev"))
            devdir = fd;
        if (grant[fd] && libdir < 0 && is_named((__wasi_fd_t)fd, &pre, "/usr/local/lib/perl5"))
            libdir = fd;
    }
}

static int is_open(int v)
{
    if (!started)
        start();
    return v >= 0 && v < FD_LIMIT && slot[v] != 0;
}

static int lowest_free(int from)
{
    for (int v = from; v < FD_LIMIT; v++) {
        if (slot[v] == 0)
            return v;
    }
    return -1;
}

/* Lets go of a virtual number, and closes the real descriptor if it was the last one. */
static __wasi_errno_t release(int v)
{
    struct file *f = &files[slot[v] - 1];
    slot[v] = 0;
    cloexec[v] = 0;
    if (--f->refs != 0 || f->real == GONE || f->real == VIRTUAL_RANDOM)
        return __WASI_ERRNO_SUCCESS;
    return __real___wasi_fd_close(f->real);
}

/* Replaces the virtual number fd, a parameter of the calling wrapper, by its real one, or
 * returns from the wrapper: EBADF for a closed number, and `on_random` for the random device,
 * which has no real one. */
#define TO_REAL_OR(fd, on_random)                                                                \
    do {                                                                                         \
        if (!is_open((int)(fd)))                                                                 \
            return __WASI_ERRNO_BADF;                                                            \
        if (files[slot[fd] - 1].real == VIRTUAL_RANDOM)                                          \
            return (on_random);                                                                  \
        (fd) = files[slot[fd] - 1].real;                                                         \
    } while (0)

/* A path call's folder: the random device is not one. */
#define TO_REAL(fd) TO_REAL_OR(fd, __WASI_ERRNO_NOTDIR)

/* A wrapper that translates the descriptor in its first argument and passes the call on. */
#define PASS_FD(name, on_random, params, args)                                                   \
    __typeof__(__wasi_##name) __real___wasi_##name, __wrap___wasi_##name;                       \
    __wasi_errno_t __wrap___wasi_##name params                                                   \
    {                                                                                            \
        TO_REAL_OR(fd, on_random);                                                               \
        return __real___wasi_##name args;                                                        \
    }

/* What Linux's character devices answer: EINVAL for the calls a device does not support,
 * ENOTDIR for a listing, ENOTSOCK for the socket calls. EBADF from the preopen calls says it
 * is not a preopened folder, as WASI's own does for every other descriptor. */
#define INVAL __WASI_ERRNO_INVAL
PASS_FD(fd_advise, INVAL, (__wasi_fd_t fd, __wasi_filesize_t offset, __wasi_filesize_t len,
                           __wasi_advice_t advice), (fd, offset, len, advice))
PASS_FD(fd_allocate, INVAL, (__wasi_fd_t fd, __wasi_filesize_t offset, __wasi_filesize_t len),
        (fd, offset, len))
PASS_FD(fd_datasync, INVAL, (__wasi_fd_t fd), (fd))
PASS_FD(fd_fdstat_set_rights, INVAL,
        (__wasi_fd_t fd, __wasi_rights_t base, __wasi_rights_t inheriting), (fd, base, inheriting))
PASS_FD(fd_filestat_set_size, INVAL, (__wasi_fd_t fd, __wasi_filesize_t size), (fd, size))
PASS_FD(fd_filestat_set_times, INVAL, (__wasi_fd_t fd, __wasi_timestamp_t atim,
                                       __wasi_timestamp_t mtim, __wasi_fstflags_t flags),
        (fd, atim, mtim, flags))
PASS_FD(fd_prestat_get, __WASI_ERRNO_BADF, (__wasi_fd_t fd, __wasi_prestat_t *buf), (fd, buf))
PASS_FD(fd_prestat_dir_name, __WASI_ERRNO_BADF,
        (__wasi_fd_t fd, uint8_t *path, __wasi_size_t path_len), (fd, path, path_len))
PASS_FD(fd_readdir, __WASI_ERRNO_NOTDIR, (__wasi_fd_t fd, uint8_t *buf, __wasi_size_t buf_len,
                                          __wasi_dircookie_t cookie, __wasi_size_t *used),
        (fd, buf, buf_len, cookie, used))
PASS_FD(fd_sync, INVAL, (__wasi_fd_t fd), (fd))
PASS_FD(path_create_directory, __WASI_ERRNO_NOTDIR, (__wasi_fd_t fd, const char *path), (fd, path))
PASS_FD(path_remove_directory, __WASI_ERRNO_NOTDIR, (__wasi_fd_t fd, const char *path), (fd, path))
PASS_FD(path_unlink_file, __WASI_ERRNO_NOTDIR, (__wasi_fd_t fd, const char *path), (fd, path))
PASS_FD(sock_recv, __WASI_ERRNO_NOTSOCK, (__wasi_fd_t fd, const __wasi_iovec_t *ri_data,
                                          size_t ri_data_len, __wasi_riflags_t ri_flags,
                                          __wasi_size_t *ro_datalen, __wasi_roflags_t *ro_flags),
        (fd, ri_data, ri_data_len, ri_flags, ro_datalen, ro_flags))
PASS_FD(sock_send, __WASI_ERRNO_NOTSOCK, (__wasi_fd_t fd, const __wasi_ciovec_t *si_data,
                                          size_t si_data_len, __wasi_siflags_t si_flags,
                                          __wasi_size_t *so_datalen),
        (fd, si_data, si_data_len, si_flags, so_datalen))
PASS_FD(sock_shutdown, __WASI_ERRNO_NOTSOCK, (__wasi_fd_t fd, __wasi_sdflags_t how), (fd, how))

/* The library index. Each `use` or `require` tries every folder of @INC in turn, and in each
 * the .pmc before the .pm, so most of the host's lookups under the library find nothing, and a
 * host lookup is far dearer than a lookup here. The build compiles into the module an index of
 * every file and folder of the library (wasi-perl-lib-index.h), and the grant the guest sees at
 * /usr/local/lib/perl5 is the library, mounted read-only. So for a path under that grant the
 * guest can know what every host would answer, and then answers itself, for a stat and for an
 * open that asks for nothing a read-only grant could refuse. Every other call is the host's.
 *
 * Every host means one whose names keep their case (Linux) and one whose names ignore it (macOS
 * and Windows, as usually set up), so the index is searched with A to Z taken as a to z, the
 * order it is sorted in. Walking the path's folders: a name no case of which is there is ENOENT
 * on every host; a file reached by exactly its own names is ENOTDIR for a path below it on every
 * host. Anything else is the host's: a name the index holds, in any case, and a path below a file
 * reached by names in another case, which one host finds and the other does not. So is every path
 * a host may read otherwise than the index would: one with an empty or `..` component (`.`
 * components are dropped), a component longer than 255 bytes, which the host refuses; a byte that
 * is not ASCII, which wasmtime refuses when it is not UTF-8; and a component Windows rewrites
 * before it looks it up, one ending in `.` or a blank or holding `~` (a short name), `\` or `:`,
 * and one whose stem, the part before its first `.`, Windows takes for a device in any case: CON,
 * PRN, AUX, NUL, COM1 to COM9, LPT1 to LPT9, CONIN$ and CONOUT$.
 * An index of no names is no index: every lookup is the host's.
 *
 * The index is the library the module was built with. A set whose library differs is the
 * embedder's to keep whole: a file added to its library is not found. */

/* The longest plain path the index judges, and the longest component a host takes. */
#define INDEX_PATH_MAX 1024
#define INDEX_NAME_MAX 255

static unsigned char fold(unsigned char c)
{
    return c >= 'A' && c <= 'Z' ? (unsigned char)(c + ('a' - 'A')) : c;
}

/* Compares the name s, with A to Z as a to z, with k[0..n) followed by `tail`: with tail 0,
 * with that string; else with the prefix of n + 1 bytes, so 0 means s starts with it. */
static int fold_cmp(const char *s, const char *k, size_t n, char tail)
{
    unsigned char a, b;
    for (size_t i = 0; i < n; i++) {
        if (s[i] == k[i])
            continue;
        a = fold((unsigned char)s[i]);
        b = fold((unsigned char)k[i]);
        if (a != b)
            return a < b ? -1 : 1;
    }
    if (tail == 0)
        return s[n] == '\0' ? 0 : 1;
    a = fold((unsigned char)s[n]);
    b = (unsigned char)tail;
    return a == b ? 0 : (a < b ? -1 : 1);
}

/* The first of names[lo..hi) not below k[0..n) and tail, compared from byte off on: every name
 * in the range has the same off bytes before it, in some case. */
static uint32_t index_find(uint32_t lo, uint32_t hi, size_t off, const char *k, size_t n,
                           char tail)
{
    const char *const *names = wasi_perl_lib_index.names;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        if (fold_cmp(names[mid] + off, k, n, tail) < 0)
            lo = mid + 1;
        else
            hi = mid;
    }
    return lo;
}

/* Whether k[0..n) is `word`, written lowercase, with A to Z as a to z. */
static int fold_is(const char *k, const char *word, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        if (fold((unsigned char)k[i]) != (unsigned char)word[i])
            return 0;
    }
    return 1;
}

/* Whether a component's stem, the part before its first `.`, is a Windows device name. Only a
 * stem of 3, 4, 6 or 7 bytes can be one, so most components cost a length. */
static int is_device(const char *p, size_t n)
{
    const char *dot = memchr(p, '.', n);
    size_t stem = dot != NULL ? (size_t)(dot - p) : n;
    switch (stem) {
    case 3:
        return fold_is(p, "con", 3) || fold_is(p, "prn", 3) || fold_is(p, "aux", 3) ||
               fold_is(p, "nul", 3);
    case 4:
        return p[3] >= '1' && p[3] <= '9' && (fold_is(p, "com", 3) || fold_is(p, "lpt", 3));
    case 6:
        return fold_is(p, "conin$", 6);
    case 7:
        return fold_is(p, "conout$", 7);
    default:
        return 0;
    }
}

/* Whether a component, not `.` or `..`, reads the same to every host. */
static int index_plain(const char *p, size_t n)
{
    if (n > INDEX_NAME_MAX || p[n - 1] == '.' || p[n - 1] == ' ' || is_device(p, n))
        return 0;
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)p[i];
        if (c >= 0x80 || c == '~' || c == '\\' || c == ':')
            return 0;
    }
    return 1;
}

/* The index's answer for `path` through the real descriptor dir: ENOENT or ENOTDIR where every
 * host gives it, or success when the host must be asked. */
static __wasi_errno_t index_answer(__wasi_fd_t dir, const char *path)
{
    const char *const *names = wasi_perl_lib_index.names;
    char buf[INDEX_PATH_MAX];
    size_t len = 0, off = 0;
    uint32_t lo = 0, hi = wasi_perl_lib_index.count;
    if (libdir < 0 || dir != (__wasi_fd_t)libdir || hi == 0)
        return __WASI_ERRNO_SUCCESS;
    for (const char *p = path;;) {
        const char *end = strchr(p, '/');
        size_t n = end != NULL ? (size_t)(end - p) : strlen(p);
        if (n == 0 || (n == 2 && p[0] == '.' && p[1] == '.'))
            return __WASI_ERRNO_SUCCESS;
        if (n != 1 || p[0] != '.') {
            if (!index_plain(p, n) || len + n + 2 > sizeof buf)
                return __WASI_ERRNO_SUCCESS;
            if (len > 0)
                buf[len++] = '/';
            memcpy(buf + len, p, n);
            len += n;
        }
        if (end == NULL)
            break;
        p = end + 1;
    }
    if (len == 0)
        return __WASI_ERRNO_SUCCESS; /* the grant itself */
    buf[len] = '\0';
    /* Each step narrows [lo, hi) to the names below the folders walked so far, which all share
     * the path's first off bytes in some case, so a comparison starts at off. */
    for (;;) {
        const char *k = buf + off;
        const char *slash = strchr(k, '/');
        size_t n = slash != NULL ? (size_t)(slash - k) : len - off;
        uint32_t m = index_find(lo, hi, off, k, n, 0), below;
        if (m == hi || fold_cmp(names[m] + off, k, n, 0) != 0)
            return __WASI_ERRNO_NOENT;
        if (slash == NULL)
            return __WASI_ERRNO_SUCCESS;
        below = index_find(m, hi, off, k, n, '/');
        if (below == hi || fold_cmp(names[below] + off, k, n, '/') != 0)
            return memcmp(names[m], buf, off + n) == 0 ? __WASI_ERRNO_NOTDIR
                                                       : __WASI_ERRNO_SUCCESS;
        hi = index_find(below, hi, off, k, n, '0');
        lo = below;
        off += n + 1;
    }
}

/* Whether an open asks for nothing a read-only grant could refuse: no create, truncation or
 * exclusive create, no right to write, and no append or sync flag. wasmtime refuses any of those
 * on a read-only grant with EPERM before it looks the name up, so only an open asking for none of
 * them gets ENOENT or ENOTDIR from the host for a name the library does not hold. */
static int asks_only_to_read(__wasi_oflags_t oflags, __wasi_rights_t base,
                             __wasi_fdflags_t fdflags)
{
    return !(oflags & (__WASI_OFLAGS_CREAT | __WASI_OFLAGS_TRUNC | __WASI_OFLAGS_EXCL)) &&
           !(base & (__WASI_RIGHTS_FD_WRITE | __WASI_RIGHTS_FD_DATASYNC |
                     __WASI_RIGHTS_FD_ALLOCATE | __WASI_RIGHTS_FD_FILESTAT_SET_SIZE)) &&
           !(fdflags & (__WASI_FDFLAGS_APPEND | __WASI_FDFLAGS_DSYNC | __WASI_FDFLAGS_RSYNC |
                        __WASI_FDFLAGS_SYNC));
}

/* The random device. Natively Perl seeds its hash randomisation, its internal random state and
 * srand from /dev/urandom, and only when that cannot be opened from the time of day, which can be
 * guessed from when a call started. WASI has no device files, so the guest serves two itself.
 * An open whose folder is the /dev grant and whose path, with `.` components dropped, is exactly
 * `urandom` or `random` makes an entry in this table that holds no host descriptor, and its
 * reads are WASI's random_get. So every caller of Perl's seed, and every program that reads the
 * device, gets the host's randomness with no change to Perl. Every other name under /dev, and
 * every path through another grant, is the host's as before; listing /dev still shows the host's
 * folder.
 *
 * It answers as Linux's /dev/urandom does for a user who is not root: reads fill every buffer and
 * are never short; writes take every byte and drop it; a seek or tell answers offset 0, as
 * Linux's noop_llseek does from a new descriptor; stat answers a character device of size 0;
 * close, dup and fcntl are table operations, and a wait finds it ready at once. */
#define URANDOM 1
#define RANDOM 2
/* Far above any inode a host gives a file under /dev, whose dev the device's stat carries. */
#define URANDOM_INO (UINT64_MAX - 9)
#define RANDOM_INO (UINT64_MAX - 8)

/* Which device a path names, relative to the grant at virtual number dir (0 for none), and in
 * *slash whether anything follows the name: a `/`, which makes a path that must be a folder, or
 * more components (`urandom/x`, `urandom/..`), which make a path through it. Linux answers
 * ENOTDIR for both. */
static int device_at(int dir, const char *path, int *slash)
{
    int which = 0;
    *slash = 0;
    if (devdir < 0 || dir != devdir)
        return 0;
    for (const char *p = path; *p != '\0';) {
        const char *end = strchr(p, '/');
        size_t n = end != NULL ? (size_t)(end - p) : strlen(p);
        if (n == 0 || (n == 1 && *p == '.') || which != 0) {
            *slash |= which != 0;
        } else if (n == 7 && memcmp(p, "urandom", 7) == 0) {
            which = URANDOM;
        } else if (n == 6 && memcmp(p, "random", 6) == 0) {
            which = RANDOM;
        } else {
            return 0;
        }
        if (end == NULL)
            break;
        *slash |= which != 0;
        p = end + 1;
    }
    return which;
}

static int is_random(int v)
{
    return is_open(v) && files[slot[v] - 1].real == VIRTUAL_RANDOM;
}

/* A character device of size 0, on the /dev grant's device and with its folder's times. */
static __wasi_errno_t random_stat(int which, __wasi_filestat_t *buf)
{
    __wasi_filestat_t dev;
    __wasi_errno_t error = __real___wasi_fd_filestat_get(files[slot[devdir] - 1].real, &dev);
    if (error != __WASI_ERRNO_SUCCESS)
        return error;
    memset(buf, 0, sizeof *buf);
    buf->dev = dev.dev;
    buf->ino = which == RANDOM ? RANDOM_INO : URANDOM_INO;
    buf->filetype = __WASI_FILETYPE_CHARACTER_DEVICE;
    buf->nlink = 1;
    buf->atim = dev.atim;
    buf->mtim = dev.mtim;
    buf->ctim = dev.ctim;
    return __WASI_ERRNO_SUCCESS;
}

/* The most one random_get is asked for. wasmtime refuses a call of more than 64 MiB with a trap,
 * and a host call runs where the runner's deadline cannot stop it, so a long read is filled in
 * pieces, each a short host call, with the loop in WebAssembly. */
#define RANDOM_PIECE 65536

/* How much of a buffer of len bytes one call moves after `total`: what still fits in its count. */
static __wasi_size_t fits(__wasi_size_t total, size_t len)
{
    return len > UINT32_MAX - total ? UINT32_MAX - total : (__wasi_size_t)len;
}

static __wasi_errno_t random_read(int v, const __wasi_iovec_t *iovs, size_t iovs_len,
                                  __wasi_size_t *nread)
{
    __wasi_size_t total = 0;
    if (!(files[slot[v] - 1].base & __WASI_RIGHTS_FD_READ))
        return __WASI_ERRNO_BADF;
    for (size_t i = 0; i < iovs_len; i++) {
        __wasi_size_t len = fits(total, iovs[i].buf_len);
        for (__wasi_size_t done = 0; done < len;) {
            __wasi_size_t piece = len - done < RANDOM_PIECE ? len - done : RANDOM_PIECE;
            __wasi_errno_t error = __wasi_random_get(iovs[i].buf + done, piece);
            if (error != __WASI_ERRNO_SUCCESS)
                return error;
            done += piece;
        }
        total += len;
    }
    *nread = total;
    return __WASI_ERRNO_SUCCESS;
}

static __wasi_errno_t random_write(int v, const __wasi_ciovec_t *iovs, size_t iovs_len,
                                   __wasi_size_t *nwritten)
{
    __wasi_size_t total = 0;
    if (!(files[slot[v] - 1].base & __WASI_RIGHTS_FD_WRITE))
        return __WASI_ERRNO_BADF;
    for (size_t i = 0; i < iovs_len; i++)
        total += fits(total, iovs[i].buf_len);
    *nwritten = total;
    return __WASI_ERRNO_SUCCESS;
}

__typeof__(__wasi_fd_read) __real___wasi_fd_read, __wrap___wasi_fd_read;
__wasi_errno_t __wrap___wasi_fd_read(__wasi_fd_t fd, const __wasi_iovec_t *iovs, size_t iovs_len,
                                     __wasi_size_t *nread)
{
    if (is_random((int)fd))
        return random_read((int)fd, iovs, iovs_len, nread);
    TO_REAL(fd);
    return __real___wasi_fd_read(fd, iovs, iovs_len, nread);
}

__typeof__(__wasi_fd_pread) __real___wasi_fd_pread, __wrap___wasi_fd_pread;
__wasi_errno_t __wrap___wasi_fd_pread(__wasi_fd_t fd, const __wasi_iovec_t *iovs, size_t iovs_len,
                                      __wasi_filesize_t offset, __wasi_size_t *nread)
{
    if (is_random((int)fd))
        return random_read((int)fd, iovs, iovs_len, nread);
    TO_REAL(fd);
    return __real___wasi_fd_pread(fd, iovs, iovs_len, offset, nread);
}

__typeof__(__wasi_fd_write) __real___wasi_fd_write, __wrap___wasi_fd_write;
__wasi_errno_t __wrap___wasi_fd_write(__wasi_fd_t fd, const __wasi_ciovec_t *iovs,
                                      size_t iovs_len, __wasi_size_t *nwritten)
{
    if (is_random((int)fd))
        return random_write((int)fd, iovs, iovs_len, nwritten);
    TO_REAL(fd);
    return __real___wasi_fd_write(fd, iovs, iovs_len, nwritten);
}

__typeof__(__wasi_fd_pwrite) __real___wasi_fd_pwrite, __wrap___wasi_fd_pwrite;
__wasi_errno_t __wrap___wasi_fd_pwrite(__wasi_fd_t fd, const __wasi_ciovec_t *iovs,
                                       size_t iovs_len, __wasi_filesize_t offset,
                                       __wasi_size_t *nwritten)
{
    if (is_random((int)fd))
        return random_write((int)fd, iovs, iovs_len, nwritten);
    TO_REAL(fd);
    return __real___wasi_fd_pwrite(fd, iovs, iovs_len, offset, nwritten);
}

__typeof__(__wasi_fd_seek) __real___wasi_fd_seek, __wrap___wasi_fd_seek;
__wasi_errno_t __wrap___wasi_fd_seek(__wasi_fd_t fd, __wasi_filedelta_t offset,
                                     __wasi_whence_t whence, __wasi_filesize_t *newoffset)
{
    if (is_random((int)fd)) {
        *newoffset = 0;
        return __WASI_ERRNO_SUCCESS;
    }
    TO_REAL(fd);
    return __real___wasi_fd_seek(fd, offset, whence, newoffset);
}

__typeof__(__wasi_fd_tell) __real___wasi_fd_tell, __wrap___wasi_fd_tell;
__wasi_errno_t __wrap___wasi_fd_tell(__wasi_fd_t fd, __wasi_filesize_t *offset)
{
    if (is_random((int)fd)) {
        *offset = 0;
        return __WASI_ERRNO_SUCCESS;
    }
    TO_REAL(fd);
    return __real___wasi_fd_tell(fd, offset);
}

/* The rights include seek and tell because wasi-libc's isatty takes a character device without
 * them for a terminal; natively -t is false on the device. Reading and writing are as the open
 * asked, so fcntl(F_GETFL) reads back the open's mode. */
__typeof__(__wasi_fd_fdstat_get) __wrap___wasi_fd_fdstat_get;
__wasi_errno_t __wrap___wasi_fd_fdstat_get(__wasi_fd_t fd, __wasi_fdstat_t *buf)
{
    if (is_random((int)fd)) {
        memset(buf, 0, sizeof *buf);
        buf->fs_filetype = __WASI_FILETYPE_CHARACTER_DEVICE;
        buf->fs_flags = files[slot[fd] - 1].fdflags;
        buf->fs_rights_base = files[slot[fd] - 1].base | __WASI_RIGHTS_FD_SEEK |
                              __WASI_RIGHTS_FD_TELL | __WASI_RIGHTS_FD_FDSTAT_SET_FLAGS |
                              __WASI_RIGHTS_FD_FILESTAT_GET | __WASI_RIGHTS_POLL_FD_READWRITE;
        return __WASI_ERRNO_SUCCESS;
    }
    TO_REAL(fd);
    return __real___wasi_fd_fdstat_get(fd, buf);
}

/* fcntl(F_SETFL) reaches here. The device never waits and has no offset to append at, so a
 * flag changes nothing but what F_GETFL reads back, as natively. */
__typeof__(__wasi_fd_fdstat_set_flags) __real___wasi_fd_fdstat_set_flags,
    __wrap___wasi_fd_fdstat_set_flags;
__wasi_errno_t __wrap___wasi_fd_fdstat_set_flags(__wasi_fd_t fd, __wasi_fdflags_t flags)
{
    if (is_random((int)fd)) {
        files[slot[fd] - 1].fdflags = flags;
        return __WASI_ERRNO_SUCCESS;
    }
    TO_REAL(fd);
    return __real___wasi_fd_fdstat_set_flags(fd, flags);
}

__typeof__(__wasi_fd_filestat_get) __real___wasi_fd_filestat_get, __wrap___wasi_fd_filestat_get;
__wasi_errno_t __wrap___wasi_fd_filestat_get(__wasi_fd_t fd, __wasi_filestat_t *buf)
{
    if (is_random((int)fd))
        return random_stat(files[slot[fd] - 1].device, buf);
    TO_REAL(fd);
    return __real___wasi_fd_filestat_get(fd, buf);
}

__typeof__(__wasi_path_filestat_get) __real___wasi_path_filestat_get,
    __wrap___wasi_path_filestat_get;
__wasi_errno_t __wrap___wasi_path_filestat_get(__wasi_fd_t fd, __wasi_lookupflags_t flags,
                                               const char *path, __wasi_filestat_t *buf)
{
    int slash = 0, which = is_open((int)fd) ? device_at((int)fd, path, &slash) : 0;
    __wasi_errno_t known;
    if (which != 0)
        return slash ? __WASI_ERRNO_NOTDIR : random_stat(which, buf);
    TO_REAL(fd);
    known = index_answer(fd, path);
    if (known != __WASI_ERRNO_SUCCESS)
        return known;
    return __real___wasi_path_filestat_get(fd, flags, path, buf);
}

/* The device is no link: readlink of it answers EINVAL, and of a path through it ENOTDIR. */
__typeof__(__wasi_path_readlink) __real___wasi_path_readlink, __wrap___wasi_path_readlink;
__wasi_errno_t __wrap___wasi_path_readlink(__wasi_fd_t fd, const char *path, uint8_t *buf,
                                           __wasi_size_t buf_len, __wasi_size_t *used)
{
    int slash = 0, which = is_open((int)fd) ? device_at((int)fd, path, &slash) : 0;
    if (which != 0)
        return slash ? __WASI_ERRNO_NOTDIR : __WASI_ERRNO_INVAL;
    TO_REAL(fd);
    return __real___wasi_path_readlink(fd, path, buf, buf_len, used);
}

/* An open of the device. As on Linux, a path that must be a folder answers ENOTDIR and an
 * exclusive create EEXIST; anything else opens it, for reading, writing or both as asked, and
 * truncating or appending changes nothing. */
static __wasi_errno_t open_random(int v, int which, int slash, __wasi_oflags_t oflags,
                                  __wasi_rights_t base, __wasi_fdflags_t fdflags,
                                  __wasi_fd_t *opened)
{
    int f;
    if (slash || (oflags & __WASI_OFLAGS_DIRECTORY))
        return __WASI_ERRNO_NOTDIR;
    if ((oflags & __WASI_OFLAGS_CREAT) && (oflags & __WASI_OFLAGS_EXCL))
        return __WASI_ERRNO_EXIST;
    f = new_file(VIRTUAL_RANDOM);
    files[f].device = (uint8_t)which;
    files[f].base = base & (__WASI_RIGHTS_FD_READ | __WASI_RIGHTS_FD_WRITE);
    files[f].fdflags = fdflags;
    bind(v, f, 0);
    *opened = (__wasi_fd_t)v;
    return __WASI_ERRNO_SUCCESS;
}

/* The calls with a descriptor in another place, or two. */

/* Setting a file's times through a link it follows makes wasmtime open the file to call
 * futimens on it, for writing and then for reading, and a FIFO's open waits for the other
 * end, past every limit (the reason open looks first). Without following, wasmtime sets the
 * times on the name through its parent folder (utimensat with AT_SYMLINK_NOFOLLOW) and opens
 * nothing. So a following call is looked at first. A folder or a regular file goes on as it
 * is. A special file that is the name itself, not a link, goes on without following, which
 * sets the same times without opening it, as natively. A link to a special file is refused
 * with ENXIO, as an open of it is. Every other host call that takes a path was checked in
 * wasmtime 51 to open nothing at the name: stat and the lookups use O_PATH or fstatat;
 * readlink, link, rename, unlink, mkdir, rmdir and symlink act through the parent folder; a
 * component in the middle of a path is opened as a folder, which a FIFO refuses at once. */
__typeof__(__wasi_path_filestat_set_times) __real___wasi_path_filestat_set_times,
    __wrap___wasi_path_filestat_set_times;
__wasi_errno_t __wrap___wasi_path_filestat_set_times(__wasi_fd_t fd, __wasi_lookupflags_t flags,
                                                     const char *path, __wasi_timestamp_t atim,
                                                     __wasi_timestamp_t mtim,
                                                     __wasi_fstflags_t fst_flags)
{
    __wasi_filestat_t target, name;
    TO_REAL(fd);
    if ((flags & __WASI_LOOKUPFLAGS_SYMLINK_FOLLOW) &&
        __real___wasi_path_filestat_get(fd, flags, path, &target) == __WASI_ERRNO_SUCCESS &&
        target.filetype != __WASI_FILETYPE_DIRECTORY &&
        target.filetype != __WASI_FILETYPE_REGULAR_FILE) {
        if (__real___wasi_path_filestat_get(fd, 0, path, &name) != __WASI_ERRNO_SUCCESS ||
            name.filetype == __WASI_FILETYPE_SYMBOLIC_LINK)
            return __WASI_ERRNO_NXIO;
        flags &= ~__WASI_LOOKUPFLAGS_SYMLINK_FOLLOW;
    }
    return __real___wasi_path_filestat_set_times(fd, flags, path, atim, mtim, fst_flags);
}

__typeof__(__wasi_path_link) __real___wasi_path_link, __wrap___wasi_path_link;
__wasi_errno_t __wrap___wasi_path_link(__wasi_fd_t fd, __wasi_lookupflags_t old_flags,
                                       const char *old_path, __wasi_fd_t new_fd,
                                       const char *new_path)
{
    TO_REAL(fd);
    TO_REAL(new_fd);
    return __real___wasi_path_link(fd, old_flags, old_path, new_fd, new_path);
}

/* Perl's in-place edit (-i) renames its work file over the file it is reading, which ARGV
 * still holds open. Linux and macOS allow that. Windows refuses to replace a file any process
 * holds open, and wasmtime reports its ERROR_ACCESS_DENIED and ERROR_SHARING_VIOLATION as
 * EACCES (EPERM for a refusal it cannot name). So when a rename fails with either, and the
 * program holds the destination open, the guest closes the real descriptors on it, renames
 * once more, and opens the destination again in each one's place, as it was opened (without
 * creating or truncating), at the same offset or the new file's end if that is shorter. The
 * virtual numbers do not change. Where the first rename succeeds, as on Linux and macOS,
 * nothing here runs. The one difference from POSIX on Windows: after such a rename, a read
 * through the old handle reads the new file, not the old one. If the retry fails, the old
 * file is opened again where it can be and the first error is returned. */
#ifdef WASI_PERL_TEST_HELD_RENAME
/* A test build only, never the shipped module: the host refuses, as Windows does, a rename
 * over a file the program holds open (tests/build/test_rename_over_open.py). */
static int holds(__wasi_fd_t realdir, const char *path);
static __wasi_errno_t host_rename(__wasi_fd_t fd, const char *old_path, __wasi_fd_t new_fd,
                                  const char *new_path)
{
    if (holds(new_fd, new_path))
        return __WASI_ERRNO_ACCES;
    return __real___wasi_path_rename(fd, old_path, new_fd, new_path);
}
#else
#define host_rename __real___wasi_path_rename
#endif

/* The open files the program holds on the regular file at this name, by device and inode. */
static int held[FD_LIMIT];
static __wasi_filesize_t held_at[FD_LIMIT];

static int find_held(__wasi_fd_t realdir, const char *path)
{
    __wasi_filestat_t target, st;
    int n = 0;
    if (__real___wasi_path_filestat_get(realdir, 0, path, &target) != __WASI_ERRNO_SUCCESS ||
        target.filetype != __WASI_FILETYPE_REGULAR_FILE)
        return 0;
    for (int i = 0; i < FD_LIMIT; i++) {
        if (files[i].refs == 0 || !files[i].reopenable)
            continue;
        if (__real___wasi_fd_filestat_get(files[i].real, &st) == __WASI_ERRNO_SUCCESS &&
            st.filetype == __WASI_FILETYPE_REGULAR_FILE && st.dev == target.dev &&
            st.ino == target.ino)
            held[n++] = i;
    }
    return n;
}

#ifdef WASI_PERL_TEST_HELD_RENAME
static int holds(__wasi_fd_t realdir, const char *path)
{
    return find_held(realdir, path) > 0;
}
#endif

__typeof__(__wasi_path_rename) __real___wasi_path_rename, __wrap___wasi_path_rename;
__wasi_errno_t __wrap___wasi_path_rename(__wasi_fd_t fd, const char *old_path, __wasi_fd_t new_fd,
                                         const char *new_path)
{
    __wasi_errno_t error, retry;
    int n;
    TO_REAL(fd);
    TO_REAL(new_fd);
    error = host_rename(fd, old_path, new_fd, new_path);
    if (error != __WASI_ERRNO_ACCES && error != __WASI_ERRNO_PERM)
        return error;
    n = find_held(new_fd, new_path);
    if (n == 0)
        return error;
    /* Each held file is opened again at the destination, so first make sure it can be: with
     * the flags it has now (fcntl may have changed them since the open) and rights no wider
     * than it has. A file that cannot be opened again is left alone, and so is the rename. */
    for (int k = 0; k < n; k++) {
        struct file *f = &files[held[k]];
        __wasi_fdstat_t now;
        __wasi_fd_t probe;
        if (__real___wasi_fd_fdstat_get(f->real, &now) != __WASI_ERRNO_SUCCESS)
            return error;
        f->fdflags = now.fs_flags;
        f->base &= now.fs_rights_base;
        f->inheriting &= now.fs_rights_inheriting;
        __wasi_oflags_t oflags =
            f->oflags & ~(__WASI_OFLAGS_CREAT | __WASI_OFLAGS_EXCL | __WASI_OFLAGS_TRUNC);
        if (__real___wasi_path_open(new_fd, 0, new_path, oflags, f->base, f->inheriting,
                                    f->fdflags, &probe) != __WASI_ERRNO_SUCCESS)
            return error;
        (void)__real___wasi_fd_close(probe);
    }
    for (int k = 0; k < n; k++) {
        struct file *f = &files[held[k]];
        if (__real___wasi_fd_tell(f->real, &held_at[k]) != __WASI_ERRNO_SUCCESS)
            held_at[k] = 0;
        (void)__real___wasi_fd_close(f->real);
        f->real = GONE;
    }
    retry = host_rename(fd, old_path, new_fd, new_path);
    /* Between the rename and the reopen another process on the host could put something else
     * at the name: the same race the look before an open leaves. So the reopen is looked at
     * first, as every open is, and what it opened must be a regular file; anything else is
     * closed again and the number answers EBADF. */
    for (int k = 0; k < n; k++) {
        struct file *f = &files[held[k]];
        __wasi_filestat_t st;
        __wasi_filesize_t pos;
        __wasi_fd_t real;
        __wasi_oflags_t oflags =
            f->oflags & ~(__WASI_OFLAGS_CREAT | __WASI_OFLAGS_EXCL | __WASI_OFLAGS_TRUNC);
        if (look(new_fd, 0, new_path, oflags, NULL) != __WASI_ERRNO_SUCCESS ||
            __real___wasi_path_open(new_fd, 0, new_path, oflags, f->base, f->inheriting,
                                    f->fdflags, &real) != __WASI_ERRNO_SUCCESS)
            continue;
        if (__real___wasi_fd_filestat_get(real, &st) != __WASI_ERRNO_SUCCESS ||
            st.filetype != __WASI_FILETYPE_REGULAR_FILE) {
            (void)__real___wasi_fd_close(real);
            continue;
        }
        f->real = real;
        pos = held_at[k] > st.size ? st.size : held_at[k];
        (void)__real___wasi_fd_seek(real, (__wasi_filedelta_t)pos, __WASI_WHENCE_SET, &pos);
    }
    return retry == __WASI_ERRNO_SUCCESS ? retry : error;
}

__typeof__(__wasi_path_symlink) __real___wasi_path_symlink, __wrap___wasi_path_symlink;
__wasi_errno_t __wrap___wasi_path_symlink(const char *old_path, __wasi_fd_t fd,
                                          const char *new_path)
{
    TO_REAL(fd);
    return __real___wasi_path_symlink(old_path, fd, new_path);
}

/* A host wait cannot be stopped: wasmtime's poll_oneoff waits in the host, where the runner's
 * epoch deadline and fuel do not reach, so `sleep 2**31` would hold the caller for good. So no
 * wait goes to the host for longer than SLICE_NS. While the soonest clock is further away, or
 * there is no clock (a wait on descriptors alone), the host is asked about the descriptors and
 * one slice of the monotonic clock, and the loop runs again in WebAssembly, where the deadline
 * and fuel stop it. The last wait, at most one slice, is the subscriptions as given with each
 * clock made relative to what remains, so the events returned are the host's for exactly
 * those subscriptions; a slice's own event never is.
 *
 * A relative wait is a duration, whatever clock it names, as it is to the host and to Linux's
 * nanosleep: its due time is kept on the monotonic clock, so a step of the wall clock neither
 * ends it early nor makes it longer. An absolute wait is a time on its own clock and is kept
 * there. An absolute wait on a clock the guest cannot read is refused with EINVAL, never sent
 * whole to the host.
 *
 * The host writes into a buffer of this function's own, one event per subscription it is
 * sent, and at most `cap` events are copied to `out`, so the host can never write past the
 * caller's buffer whatever is added here. With `own_clock`, the caller's only clock is one the
 * table added (a zero timeout), and its event is dropped too. */
#define SLICE_NS 10000000ULL
#define OWN_CLOCK UINT64_MAX

__typeof__(__wasi_poll_oneoff) __real___wasi_poll_oneoff;

static __wasi_errno_t sliced_poll(const __wasi_subscription_t *subs, __wasi_size_t n,
                                  __wasi_event_t *out, __wasi_size_t cap, int own_clock,
                                  __wasi_size_t *nevents)
{
    __wasi_timestamp_t *due = malloc(n * sizeof *due);
    uint8_t *relative = malloc(n);
    __wasi_subscription_t *work = malloc((n + 1) * sizeof *work);
    __wasi_event_t *ev = malloc((n + 1) * sizeof *ev);
    __wasi_errno_t error = __WASI_ERRNO_SUCCESS;
    __wasi_size_t got = 0, kept = 0;
    if (due == NULL || relative == NULL || work == NULL || ev == NULL) {
        error = __WASI_ERRNO_NOMEM;
        goto done;
    }
    for (__wasi_size_t i = 0; i < n; i++) {
        const __wasi_subscription_clock_t *c = &subs[i].u.u.clock;
        __wasi_timestamp_t now;
        if (subs[i].u.tag != __WASI_EVENTTYPE_CLOCK)
            continue;
        relative[i] = !(c->flags & __WASI_SUBCLOCKFLAGS_SUBSCRIPTION_CLOCK_ABSTIME);
        if (__wasi_clock_time_get(relative[i] ? __WASI_CLOCKID_MONOTONIC : c->id, 1, &now) !=
            __WASI_ERRNO_SUCCESS) {
            error = __WASI_ERRNO_INVAL;
            goto done;
        }
        if (!relative[i])
            due[i] = c->timeout;
        else
            due[i] = c->timeout > UINT64_MAX - now ? UINT64_MAX : now + c->timeout;
    }
    for (;;) {
        __wasi_timestamp_t soonest = UINT64_MAX, mono = 0;
        __wasi_size_t m = 0;
        (void)__wasi_clock_time_get(__WASI_CLOCKID_MONOTONIC, 1, &mono);
        for (__wasi_size_t i = 0; i < n; i++) {
            __wasi_timestamp_t now = mono, left;
            work[i] = subs[i];
            if (subs[i].u.tag != __WASI_EVENTTYPE_CLOCK)
                continue;
            if (!relative[i])
                (void)__wasi_clock_time_get(subs[i].u.u.clock.id, 1, &now);
            left = due[i] > now ? due[i] - now : 0;
            work[i].u.u.clock.timeout = left;
            work[i].u.u.clock.flags &= ~__WASI_SUBCLOCKFLAGS_SUBSCRIPTION_CLOCK_ABSTIME;
            if (left < soonest)
                soonest = left;
        }
        if (soonest <= SLICE_NS) {
            error = __real___wasi_poll_oneoff(work, ev, n, &got);
            break;
        }
        for (__wasi_size_t i = 0; i < n; i++) {
            if (subs[i].u.tag != __WASI_EVENTTYPE_CLOCK)
                work[m++] = subs[i];
        }
        memset(&work[m], 0, sizeof work[m]);
        work[m].userdata = OWN_CLOCK;
        work[m].u.tag = __WASI_EVENTTYPE_CLOCK;
        work[m].u.u.clock.id = __WASI_CLOCKID_MONOTONIC;
        work[m].u.u.clock.timeout = SLICE_NS;
        m++;
        error = __real___wasi_poll_oneoff(work, ev, m, &got);
        if (error != __WASI_ERRNO_SUCCESS)
            break;
        /* The slice is the only clock in this call, so any clock event is its own. */
        for (__wasi_size_t k = 0; k < got && k < m; k++) {
            if (ev[k].type != __WASI_EVENTTYPE_CLOCK)
                ev[kept++] = ev[k];
        }
        got = kept;
        kept = 0;
        if (got > 0)
            break;
    }
done:
    if (error == __WASI_ERRNO_SUCCESS) {
        for (__wasi_size_t k = 0; k < got && k < n + 1 && kept < cap; k++) {
            if (own_clock && ev[k].type == __WASI_EVENTTYPE_CLOCK)
                continue;
            out[kept++] = ev[k];
        }
        *nevents = kept;
    }
    free(due);
    free(relative);
    free(work);
    free(ev);
    return error;
}

/* poll_oneoff names descriptors inside its subscriptions. Two kinds are answered here and not
 * sent to the host. A subscription on a closed number gets its own event, EBADF with its
 * userdata, which wasi-libc's poll turns into POLLNVAL for that entry alone, as natively; the
 * host would fail the whole call. A regular file is always ready to read and to write, as
 * POSIX says of select and poll; the host would report it only once a read it starts in the
 * background is done, which a zero timeout can outrun. So is the random device, which has no
 * host descriptor and whose reads and writes never wait. A third kind is not sent at all: a
 * read on a descriptor without the right to read, or a write on one without the right to
 * write (a copy of STDOUT asked whether it is readable). The host fails the whole call for
 * those; natively that entry is simply never ready. The host's events follow these in `out`. */
__typeof__(__wasi_poll_oneoff) __real___wasi_poll_oneoff, __wrap___wasi_poll_oneoff;
__wasi_errno_t __wrap___wasi_poll_oneoff(const __wasi_subscription_t *in, __wasi_event_t *out,
                                         __wasi_size_t nsubscriptions, __wasi_size_t *nevents)
{
    __wasi_subscription_t *copy;
    __wasi_errno_t error = __WASI_ERRNO_SUCCESS;
    __wasi_size_t nlocal = 0, nsent = 0, nhost = 0;
    int has_fd = 0;
    for (__wasi_size_t i = 0; i < nsubscriptions; i++)
        has_fd |= in[i].u.tag != __WASI_EVENTTYPE_CLOCK;
    if (!has_fd)
        return sliced_poll(in, nsubscriptions, out, nsubscriptions, 0, nevents);
    copy = malloc(nsubscriptions * sizeof *copy);
    if (copy == NULL)
        return __WASI_ERRNO_NOMEM;
    for (__wasi_size_t i = 0; i < nsubscriptions; i++) {
        /* fd_read and fd_write have the same layout. */
        __wasi_fd_t fd = in[i].u.u.fd_read.file_descriptor;
        __wasi_fdstat_t st;
        int open = in[i].u.tag != __WASI_EVENTTYPE_CLOCK && is_open((int)fd) &&
                   files[slot[fd] - 1].real != GONE;
        int device = open && files[slot[fd] - 1].real == VIRTUAL_RANDOM;
        int known = open && !device &&
                    __real___wasi_fd_fdstat_get(files[slot[fd] - 1].real, &st) == 0;
        if (known && st.fs_filetype != __WASI_FILETYPE_REGULAR_FILE &&
            !(st.fs_rights_base & (in[i].u.tag == __WASI_EVENTTYPE_FD_READ
                                       ? __WASI_RIGHTS_FD_READ
                                       : __WASI_RIGHTS_FD_WRITE)))
            continue;
        if (in[i].u.tag != __WASI_EVENTTYPE_CLOCK &&
            (!open || device || (known && st.fs_filetype == __WASI_FILETYPE_REGULAR_FILE))) {
            memset(&out[nlocal], 0, sizeof out[nlocal]);
            out[nlocal].userdata = in[i].userdata;
            out[nlocal].error = open ? __WASI_ERRNO_SUCCESS : __WASI_ERRNO_BADF;
            out[nlocal].type = in[i].u.tag;
            out[nlocal].fd_readwrite.nbytes = 1;
            nlocal++;
            continue;
        }
        copy[nsent] = in[i];
        if (in[i].u.tag != __WASI_EVENTTYPE_CLOCK)
            copy[nsent].u.u.fd_read.file_descriptor = files[slot[fd] - 1].real;
        nsent++;
    }
    /* With an event answered here there is something to report already, so the host is asked
     * whether anything else is ready without waiting: a clock subscription would make it
     * wait. */
    if (nlocal > 0) {
        __wasi_size_t keep = 0;
        for (__wasi_size_t i = 0; i < nsent; i++) {
            if (copy[i].u.tag != __WASI_EVENTTYPE_CLOCK)
                copy[keep++] = copy[i];
        }
        nsent = keep;
        if (nsent > 0) {
            __wasi_subscription_t *now = &copy[nsent++];
            memset(now, 0, sizeof *now);
            now->userdata = OWN_CLOCK;
            now->u.tag = __WASI_EVENTTYPE_CLOCK;
            now->u.u.clock.id = __WASI_CLOCKID_MONOTONIC;
        }
    }
    if (nsent > 0)
        error = sliced_poll(copy, nsent, out + nlocal, nsubscriptions - nlocal, nlocal > 0,
                            &nhost);
    free(copy);
    if (error != __WASI_ERRNO_SUCCESS)
        return error;
    *nevents = nlocal + nhost;
    return __WASI_ERRNO_SUCCESS;
}

/* A host's open of a FIFO waits in the kernel for the other end, where neither the runner's
 * deadline nor its fuel can stop it: they stop WebAssembly code only. So before the host is
 * asked to open a name, the guest asks what the name is, with the same directory, lookup flags
 * and path, so it resolves as the open will (following links exactly when the open does).
 * Anything but a folder, a regular file, or a link the open will not follow is refused:
 * ENOTDIR when the open must find a folder (what the host answers for any other non-folder),
 * ENXIO otherwise (what a host gives for a device it will not open). A name that does not
 * exist, or that the host will not describe, is left to the open, so creating files works and
 * every other answer stays the host's. An exclusive create needs no look: the host refuses an
 * existing name before it opens anything. The guest's only code is Perl, which cannot call a WASI import directly, so no
 * program can skip the look. What it cannot close is another process on the host swapping the
 * name between the look and the open. When regular is not NULL, it is set to whether the look
 * saw a regular file. */
static __wasi_errno_t look(__wasi_fd_t realdir, __wasi_lookupflags_t dirflags, const char *path,
                           __wasi_oflags_t oflags, uint8_t *regular)
{
    __wasi_filestat_t st;
    if (regular)
        *regular = 0;
    if ((oflags & __WASI_OFLAGS_CREAT) && (oflags & __WASI_OFLAGS_EXCL))
        return __WASI_ERRNO_SUCCESS;
    if (__real___wasi_path_filestat_get(realdir, dirflags, path, &st) != __WASI_ERRNO_SUCCESS)
        return __WASI_ERRNO_SUCCESS;
    if (regular)
        *regular = st.filetype == __WASI_FILETYPE_REGULAR_FILE;
    switch (st.filetype) {
    case __WASI_FILETYPE_DIRECTORY:
    case __WASI_FILETYPE_REGULAR_FILE:
    case __WASI_FILETYPE_SYMBOLIC_LINK:
        return __WASI_ERRNO_SUCCESS;
    default:
        return (oflags & __WASI_OFLAGS_DIRECTORY) ? __WASI_ERRNO_NOTDIR : __WASI_ERRNO_NXIO;
    }
}

/* The calls that make a new descriptor. A free number is found first, so a full table refuses
 * the call without opening anything. */
__typeof__(__wasi_path_open) __real___wasi_path_open, __wrap___wasi_path_open;
__wasi_errno_t __wrap___wasi_path_open(__wasi_fd_t fd, __wasi_lookupflags_t dirflags,
                                       const char *path, __wasi_oflags_t oflags,
                                       __wasi_rights_t base, __wasi_rights_t inheriting,
                                       __wasi_fdflags_t fdflags, __wasi_fd_t *opened)
{
    __wasi_fd_t real;
    __wasi_errno_t error;
    int v, f, slash = 0, which = is_open((int)fd) ? device_at((int)fd, path, &slash) : 0;
    uint8_t regular;
    TO_REAL(fd);
    v = lowest_free(0);
    if (v < 0)
        return __WASI_ERRNO_MFILE;
    if (which != 0)
        return open_random(v, which, slash, oflags, base, fdflags, opened);
    if (asks_only_to_read(oflags, base, fdflags)) {
        error = index_answer(fd, path);
        if (error != __WASI_ERRNO_SUCCESS)
            return error;
    }
    error = look(fd, dirflags, path, oflags, &regular);
    if (error != __WASI_ERRNO_SUCCESS)
        return error;
    error = __real___wasi_path_open(fd, dirflags, path, oflags, base, inheriting, fdflags, &real);
    if (error != __WASI_ERRNO_SUCCESS)
        return error;
    f = new_file(real);
    files[f].oflags = oflags;
    files[f].fdflags = fdflags;
    files[f].base = base;
    files[f].inheriting = inheriting;
    files[f].reopenable = 1;
    files[f].regular = regular;
    bind(v, f, 0);
    *opened = (__wasi_fd_t)v;
    return __WASI_ERRNO_SUCCESS;
}

__typeof__(__wasi_sock_accept) __real___wasi_sock_accept, __wrap___wasi_sock_accept;
__wasi_errno_t __wrap___wasi_sock_accept(__wasi_fd_t fd, __wasi_fdflags_t flags,
                                         __wasi_fd_t *accepted)
{
    __wasi_fd_t real;
    __wasi_errno_t error;
    int v;
    TO_REAL_OR(fd, __WASI_ERRNO_NOTSOCK);
    v = lowest_free(0);
    if (v < 0)
        return __WASI_ERRNO_MFILE;
    error = __real___wasi_sock_accept(fd, flags, &real);
    if (error != __WASI_ERRNO_SUCCESS)
        return error;
    bind(v, new_file(real), 0);
    *accepted = (__wasi_fd_t)v;
    return __WASI_ERRNO_SUCCESS;
}

/* close() reaches here. */
__typeof__(__wasi_fd_close) __wrap___wasi_fd_close;
__wasi_errno_t __wrap___wasi_fd_close(__wasi_fd_t fd)
{
    if (!is_open((int)fd))
        return __WASI_ERRNO_BADF;
    if (grant[fd])
        return __WASI_ERRNO_SUCCESS;
    return release((int)fd);
}

/* Moves one open number onto another, closing the other first (wasi-libc's freopen uses
 * this). It is a table operation; the host is told only if a real descriptor closes. A grant's
 * number is neither moved nor replaced. */
__typeof__(__wasi_fd_renumber) __wrap___wasi_fd_renumber;
__wasi_errno_t __wrap___wasi_fd_renumber(__wasi_fd_t fd, __wasi_fd_t to)
{
    if (!is_open((int)fd) || !is_open((int)to) || grant[fd] || grant[to])
        return __WASI_ERRNO_BADF; /* as WASI's own, which needs both open */
    if (fd == to)
        return __WASI_ERRNO_SUCCESS;
    /* wasi-libc renumbers only to put a newly opened host file in an old one's place. */
    if (files[slot[fd] - 1].real == VIRTUAL_RANDOM && files[slot[to] - 1].real != VIRTUAL_RANDOM)
        return __WASI_ERRNO_INVAL;
    (void)release((int)to);
    slot[to] = slot[fd];
    cloexec[to] = cloexec[fd];
    slot[fd] = 0;
    cloexec[fd] = 0;
    return __WASI_ERRNO_SUCCESS;
}

/* dup, dup2, dup3 and fcntl(F_DUPFD, F_DUPFD_CLOEXEC): a new number for the same open file. */
static int dup_from(int fd, int from, int close_on_exec)
{
    int v;
    if (!is_open(fd)) {
        errno = EBADF;
        return -1;
    }
    v = lowest_free(from);
    if (v < 0) {
        errno = EMFILE;
        return -1;
    }
    bind(v, slot[fd] - 1, close_on_exec);
    return v;
}

int __wrap_dup(int fd);
int __wrap_dup(int fd)
{
    return dup_from(fd, 0, 0);
}

/* As POSIX says: a no-op when the numbers are equal, and otherwise newfd is closed first,
 * quietly. */
static int dup_onto(int fd, int newfd, int close_on_exec)
{
    if (!is_open(fd) || newfd < 0 || newfd >= FD_LIMIT || (grant[newfd] && fd != newfd)) {
        errno = EBADF;
        return -1;
    }
    if (fd == newfd)
        return newfd;
    if (slot[newfd] != 0)
        (void)release(newfd);
    bind(newfd, slot[fd] - 1, close_on_exec);
    return newfd;
}

int __wrap_dup2(int fd, int newfd);
int __wrap_dup2(int fd, int newfd)
{
    return dup_onto(fd, newfd, 0);
}

/* wasi-libc's O_CLOEXEC is 0, so no flag can be asked for, and any other flag is invalid. */
int __wrap_dup3(int fd, int newfd, int flags);
int __wrap_dup3(int fd, int newfd, int flags)
{
    if (fd == newfd || (flags & ~O_CLOEXEC) != 0) {
        errno = EINVAL;
        return -1;
    }
    return dup_onto(fd, newfd, (flags & O_CLOEXEC) != 0);
}

/* The third argument is read only for the commands that take one. wasi-libc's fcntl acts as
 * if FD_CLOEXEC were always set and does not look at the descriptor; here F_GETFD and
 * F_SETFD use the table, so a closed number fails with EBADF and the flag reads back as set.
 * Every other command is wasi-libc's. */
int __real_fcntl(int fd, int cmd, ...);
int __wrap_fcntl(int fd, int cmd, ...);
int __wrap_fcntl(int fd, int cmd, ...)
{
    va_list ap;
    int arg = 0;
    if (cmd == F_DUPFD || cmd == F_DUPFD_CLOEXEC || cmd == F_SETFD || cmd == F_SETFL) {
        va_start(ap, cmd);
        arg = va_arg(ap, int);
        va_end(ap);
    }
    switch (cmd) {
    case F_DUPFD:
    case F_DUPFD_CLOEXEC:
        if (arg < 0 || arg >= FD_LIMIT) {
            errno = EINVAL;
            return -1;
        }
        return dup_from(fd, arg, cmd == F_DUPFD_CLOEXEC);
    case F_GETFD:
    case F_SETFD:
        if (!is_open(fd)) {
            errno = EBADF;
            return -1;
        }
        if (cmd == F_GETFD)
            return cloexec[fd] ? FD_CLOEXEC : 0;
        cloexec[fd] = (arg & FD_CLOEXEC) != 0;
        return 0;
    case F_SETFL:
        return __real_fcntl(fd, cmd, arg);
    default:
        return __real_fcntl(fd, cmd);
    }
}

/* An open with no access mode reads, as on Linux, where O_RDONLY is 0. C written for Linux
 * passes 0 for a read: Perl's own seed opens /dev/urandom so, and so does a program's
 * sysopen(F, $path, 0). wasi-libc's O_RDONLY is a bit of its own, and it refuses an open without
 * one with EINVAL before any call reaches the host. So when the access-mode bits are all clear,
 * O_RDONLY is added; nothing else about the open changes. The mode is read only when the open
 * may create the file, as POSIX says. */
int __real_open(const char *path, int oflag, ...);
int __wrap_open(const char *path, int oflag, ...);
int __wrap_open(const char *path, int oflag, ...)
{
    mode_t mode = 0;
    if (oflag & O_CREAT) {
        va_list ap;
        va_start(ap, oflag);
        mode = va_arg(ap, mode_t);
        va_end(ap);
    }
    if ((oflag & O_ACCMODE) == 0)
        oflag |= O_RDONLY;
    return __real_open(path, oflag, mode);
}

int __real_openat(int fd, const char *path, int oflag, ...);
int __wrap_openat(int fd, const char *path, int oflag, ...);
int __wrap_openat(int fd, const char *path, int oflag, ...)
{
    mode_t mode = 0;
    if (oflag & O_CREAT) {
        va_list ap;
        va_start(ap, oflag);
        mode = va_arg(ap, mode_t);
        va_end(ap);
    }
    if ((oflag & O_ACCMODE) == 0)
        oflag |= O_RDONLY;
    return __real_openat(fd, path, oflag, mode);
}

/* Four-argument select. Perl hands select the strings vec() builds, one bit per descriptor, as
 * its fd_set buffers (pp_sselect, the only caller of select in the link), and pads each to
 * whole words past nfds / 8 bytes. wasi-libc's fd_set is a count and a list of descriptors. So
 * the bits are read into wasi-libc's sets, its select runs, and the descriptors it reports are
 * written back as bits. As on Linux, which reads no further than its descriptor table, bits at
 * or past the table's size are not looked at and are left as they were. On an error the
 * vectors are left as they were, as POSIX says. */
__typeof__(select) __real_select, __wrap_select;
int __wrap_select(int nfds, fd_set *restrict readfds, fd_set *restrict writefds,
                  fd_set *restrict errorfds, struct timeval *restrict timeout)
{
    static fd_set sets[3];
    unsigned char *bits[3] = {(unsigned char *)readfds, (unsigned char *)writefds,
                              (unsigned char *)errorfds};
    fd_set *args[3];
    int n, found;
    if (nfds < 0) {
        errno = EINVAL;
        return -1;
    }
    n = nfds < FD_LIMIT ? nfds : FD_LIMIT;
    /* No descriptor here has an exceptional condition to report, and wasi-libc answers
     * ENOSYS for an error set with nothing else to watch, so the error set is never passed:
     * on success its bits come back clear, as natively. */
    for (int k = 0; k < 3; k++) {
        args[k] = NULL;
        if (bits[k] == NULL || k == 2)
            continue;
        args[k] = &sets[k];
        FD_ZERO(args[k]);
        for (int fd = 0; fd < n; fd++) {
            if (bits[k][fd / 8] & (1u << (fd % 8)))
                FD_SET(fd, args[k]);
        }
    }
    found = __real_select(n, args[0], args[1], args[2], timeout);
    if (found < 0)
        return found;
    for (int k = 0; k < 3; k++) {
        if (bits[k] == NULL)
            continue;
        for (int fd = 0; fd < n; fd++) {
            if (args[k] != NULL && FD_ISSET(fd, args[k]))
                bits[k][fd / 8] |= (unsigned char)(1u << (fd % 8));
            else
                bits[k][fd / 8] &= (unsigned char)~(1u << (fd % 8));
        }
    }
    return found;
}

/* isatty asks the host for the descriptor's type (fd_fdstat_get), and PerlIO asks it of every
 * file it opens. A file whose look before the open saw a regular file is no terminal, so the
 * answer is given here, with the errno wasi-libc and Linux give. Every other descriptor asks the
 * host: the standard ones, folders, a file created by the open (there was nothing to look at),
 * and a file whose host descriptor was lost to a rename over it. A name swapped between the
 * look and the open is the race the look already leaves: the worst it can do is make a terminal
 * buffered as a file, and no terminal can be reached through a grant. */
int __real_isatty(int fd);
int __wrap_isatty(int fd);
int __wrap_isatty(int fd)
{
    if (is_open(fd) && files[slot[fd] - 1].regular && files[slot[fd] - 1].real != GONE) {
        errno = ENOTTY;
        return 0;
    }
    return __real_isatty(fd);
}
