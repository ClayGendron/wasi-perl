"""Summarise wasmtime guest profiles (Firefox processed format) as top-N self and inclusive
tables. Usage: profsum.py N PROFILE.json [PROFILE.json ...]; several profiles of one workload
are pooled, since each sample is one stack."""

import json
import sys
from collections import Counter


def stacks(path):
    p = json.load(open(path))
    for t in p["threads"]:
        st, ft, fn, sa = t["stackTable"], t["frameTable"], t["funcTable"], t["stringArray"]
        names = [sa[i] for i in fn["name"]]
        frame_func = ft["func"]
        prefix, sframe = st["prefix"], st["frame"]
        memo = {}

        def chain(s0):
            if s0 not in memo:
                out, s = [], s0
                while s is not None:
                    out.append(names[frame_func[sframe[s]]])
                    s = prefix[s]
                memo[s0] = out
            return memo[s0]

        for s0 in t["samples"]["stack"]:
            if s0 is None:
                continue
            yield chain(s0)


def main():
    n = int(sys.argv[1])
    self_c, incl_c, total = Counter(), Counter(), 0
    for path in sys.argv[2:]:
        for ch in stacks(path):
            total += 1
            self_c[ch[0]] += 1
            for f in set(ch):
                incl_c[f] += 1
    print(f"samples: {total}")
    print("| function | self % | inclusive % |")
    print("|---|---:|---:|")
    for f, c in self_c.most_common(n):
        print(f"| `{f}` | {100 * c / total:.1f} | {100 * incl_c[f] / total:.1f} |")




def callers(fname, paths):
    """The immediate callers of FNAME, by share of FNAME's own samples."""
    c, total = Counter(), 0
    for path in paths:
        for ch in stacks(path):
            if ch[0] == fname:
                total += 1
                c[" < ".join(ch[1:4])] += 1
    print(f"{fname}: {total} self samples")
    for k, v in c.most_common(6):
        print(f"  {100 * v / total:.0f}%  {k}")


if __name__ == "__main__":
    if sys.argv[1] == "callers":
        callers(sys.argv[2], sys.argv[3:])
    else:
        main()
