"""Whether a write through a shared mapping moves a file's stamp, which a record of stamps trusts.

    python mmap_stamp.py <scratch file> <seconds>

Writes the file, maps it shared and writable, and writes one byte through the mapping (a write
fault, which stamps the file). It waits <seconds>, past the two-second window, then reads the
file and its stamp (st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns) as a check would, and
writes the same page again. Prints one JSON line: whether the bytes changed, and whether the
stamp moved, after the second write, after the unmap, and after a sync.
"""

import hashlib
import json
import mmap
import os
import sys
import time

path, wait = sys.argv[1], float(sys.argv[2])
with open(path, "wb") as f:
    f.write(b"package strict; 1;\n" + b"#" * 4000)


def stamp():
    st = os.stat(path)
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]


def digest():
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


fd = os.open(path, os.O_RDWR)
m = mmap.mmap(fd, 0, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
m[0:1] = b"p"
time.sleep(wait)
checked, checked_digest = stamp(), digest()  # what a check would hash and record
m[0:7] = b"PACKAGE"  # the same page, already dirty: no fault
after_write = stamp()
m.close()
os.close(fd)
after_unmap = stamp()
os.sync()
after_sync = stamp()
print(json.dumps({
    "bytes_changed": digest() != checked_digest,
    "stamp_moved_by_the_write": after_write != checked,
    "stamp_moved_by_the_unmap": after_unmap != checked,
    "stamp_moved_by_the_sync": after_sync != checked,
    "stamp": checked,
}))
os.unlink(path)
