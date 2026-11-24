/* What one hand-off to another thread and back costs on this machine, with no wasmtime in it:
 * the main thread posts a job to a waiting worker through a mutex and condition variable and
 * waits for the answer, as tokio's blocking pool and a parked caller do. Prints microseconds per
 * round trip.
 *
 *     cc -O2 -pthread handoff.c -o handoff && ./handoff 20000
 */
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

static pthread_mutex_t m = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t to_worker = PTHREAD_COND_INITIALIZER, to_main = PTHREAD_COND_INITIALIZER;
static long job, done;

static void *worker(void *arg) {
    (void)arg;
    pthread_mutex_lock(&m);
    for (;;) {
        while (job == done) pthread_cond_wait(&to_worker, &m);
        done = job;
        pthread_cond_signal(&to_main);
    }
    return NULL;
}

int main(int argc, char **argv) {
    long n = argc > 1 ? atol(argv[1]) : 20000;
    pthread_t t;
    pthread_create(&t, NULL, worker, NULL);
    struct timespec a, b;
    clock_gettime(CLOCK_MONOTONIC, &a);
    pthread_mutex_lock(&m);
    for (long i = 0; i < n; i++) {
        job++;
        pthread_cond_signal(&to_worker);
        while (done != job) pthread_cond_wait(&to_main, &m);
    }
    pthread_mutex_unlock(&m);
    clock_gettime(CLOCK_MONOTONIC, &b);
    double us = ((b.tv_sec - a.tv_sec) * 1e9 + (b.tv_nsec - a.tv_nsec)) / 1e3 / n;
    printf("handoff n=%ld us_per_round_trip=%.2f\n", n, us);
    return 0;
}
