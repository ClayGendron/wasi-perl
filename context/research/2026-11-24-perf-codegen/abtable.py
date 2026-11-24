"""A paired-timing result as a table: each variant's median ms per call over its rounds, as a
ratio to the first variant's (the min ratio in brackets). Usage: abtable.py results/ab-*.json"""

import json
import sys

r = json.load(open(sys.argv[1]))
names = r["variants"]
works = [k for k in r if k not in ("rounds", "variants")]
ref = names[0]
print(f"{r['rounds']} rounds; median ratio to {ref} [min ratio]")
print("variant".ljust(10), " ".join(w[:8].rjust(13) for w in works))
for n in names:
    print(
        n.ljust(10),
        " ".join(
            f"{r[w][n]['median_ms'] / r[w][ref]['median_ms']:6.3f}"
            f"[{r[w][n]['min_ms'] / r[w][ref]['min_ms']:5.3f}]"
            for w in works
        ),
    )
print(f"{ref} median ms:", " ".join(f"{w}={r[w][ref]['median_ms']}" for w in works))
