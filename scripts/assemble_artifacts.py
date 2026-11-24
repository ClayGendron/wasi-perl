"""Copy the built artifact set into the package, so `uv build` puts it in the wheel.

The set is build/artifacts/: perl.wasm, the library folder lib/, the dev/ folder holding the empty
null file, MANIFEST and SHA256SUMS. Perl's licence notices, Artistic and Copying, come from the
target tree build/target/, or from the pinned tarball when that tree is gone.

Nothing is copied unless every hash agrees: SHA256SUMS over perl.wasm and MANIFEST, and MANIFEST
over every file under lib/ and dev/, with no file left unlisted and no symbolic link anywhere. The
copy is checked again in place, so a wheel never carries bytes the build did not hash.

Run with `uv run --no-project python scripts/assemble_artifacts.py`.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
import tarfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
SET_PARTS = ("perl.wasm", "lib", "dev", "MANIFEST", "SHA256SUMS")
LICENCES = ("Artistic", "Copying")
LINE = re.compile(r"^([0-9a-f]{64}) [ *](.+)$")


class Refused(Exception):
    """The artifact set does not verify; nothing is copied."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_sums(path: Path) -> dict[str, str]:
    """Read `<sha256>  <relative path>` lines; refuse a path that could leave the set."""
    sums: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        match = LINE.match(line)
        if not match:
            raise Refused(f"{path.name} line {number} is not '<sha256>  <path>'")
        digest, name = match.groups()
        pure = PurePosixPath(name)
        if pure.is_absolute() or ".." in pure.parts or "\\" in name:
            raise Refused(f"{path.name} line {number} names a path outside the set: {name}")
        if name in sums:
            raise Refused(f"{path.name} lists {name} twice")
        sums[name] = digest
    return sums


def check(base: Path, sums: dict[str, str], listed_in: str) -> None:
    for name, want in sums.items():
        file = base / name
        if not file.is_file():
            raise Refused(f"{listed_in} lists {name}, which is not in {base}")
        if sha256(file) != want:
            raise Refused(f"{name} does not match its SHA-256 in {listed_in}")


def verify(base: Path) -> int:
    """Verify one artifact set in place and return its file count."""
    for part in SET_PARTS:
        if not (base / part).exists():
            raise Refused(f"{base} has no {part}; run scripts/build.sh first")
    links = [p for p in base.rglob("*") if p.is_symlink()]
    if links:
        raise Refused(f"{links[0]} is a symbolic link; the set holds plain files only")

    sums = read_sums(base / "SHA256SUMS")
    for needed in ("perl.wasm", "MANIFEST"):
        if needed not in sums:
            raise Refused(f"SHA256SUMS does not hash {needed}")
    check(base, sums, "SHA256SUMS")

    manifest = read_sums(base / "MANIFEST")
    check(base, manifest, "MANIFEST")
    on_disk = {
        p.relative_to(base).as_posix()
        for folder in ("lib", "dev")
        for p in (base / folder).rglob("*")
        if p.is_file()
    }
    unlisted = sorted(on_disk - manifest.keys())
    if unlisted:
        raise Refused(f"{unlisted[0]} is in the set but not in MANIFEST ({len(unlisted)} such)")
    if (base / "dev" / "null").stat().st_size != 0:
        raise Refused("dev/null is not empty")
    return len(on_disk | sums.keys())


def licence_texts(target: Path, downloads: Path) -> dict[str, bytes]:
    """Perl's licence notices, from the target tree or else from the verified tarball."""
    if all((target / name).is_file() for name in LICENCES):
        return {name: (target / name).read_bytes() for name in LICENCES}
    if not (downloads / "SHA256SUMS").is_file():
        raise Refused(f"no {target}/Artistic and no {downloads}/SHA256SUMS to find the tarball by")
    pins = {
        line.split()[1]: line.split()[0]
        for line in (downloads / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    tarballs = [name for name in pins if re.fullmatch(r"perl-[0-9.]+\.tar\.xz", name)]
    if len(tarballs) != 1:
        raise Refused("downloads/SHA256SUMS does not pin exactly one Perl tarball")
    tarball = downloads / tarballs[0]
    if not tarball.is_file() or sha256(tarball) != pins[tarballs[0]]:
        raise Refused(f"{tarball} is missing or does not match downloads/SHA256SUMS")
    top = tarballs[0].removesuffix(".tar.xz")
    texts: dict[str, bytes] = {}
    with tarfile.open(tarball, "r:xz") as tar:
        for name in LICENCES:
            member = tar.extractfile(f"{top}/{name}")
            if member is None:
                raise Refused(f"{tarball.name} has no {top}/{name}")
            texts[name] = member.read()
    return texts


def assemble(source: Path, dest: Path, target: Path, downloads: Path) -> str:
    count = verify(source)
    texts = licence_texts(target, downloads)
    staging = dest.with_name(dest.name + ".part")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    for part in SET_PARTS:
        here = source / part
        if here.is_dir():
            shutil.copytree(here, staging / part)
        else:
            shutil.copy2(here, staging / part)
    for name, text in texts.items():
        (staging / name).write_bytes(text)
    verify(staging)
    shutil.rmtree(dest, ignore_errors=True)
    staging.rename(dest)
    size = sum(p.stat().st_size for p in dest.rglob("*") if p.is_file())
    return f"assembled {count} files, {size} bytes, into {dest}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, default=ROOT / "build" / "artifacts")
    parser.add_argument("--dest", type=Path, default=ROOT / "src" / "wasi_perl" / "artifacts")
    parser.add_argument("--target", type=Path, default=ROOT / "build" / "target")
    parser.add_argument("--downloads", type=Path, default=ROOT / "downloads")
    args = parser.parse_args(argv)
    try:
        print(assemble(args.source, args.dest, args.target, args.downloads))
    except Refused as refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
