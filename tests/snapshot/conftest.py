"""The preload image's fixtures: the two images every drift and reset check compares with
perl.wasm, made once per session."""

from __future__ import annotations

import pytest

from snapshot.images import image_set


@pytest.fixture(scope="session")
def empty_image():
    from wasi_perl import make_snapshot

    return make_snapshot("", artifacts=image_set(), cache_dir=False)


@pytest.fixture(scope="session")
def package_image():
    from wasi_perl import make_package_snapshot

    return make_package_snapshot(artifacts=image_set(), cache_dir=False)


@pytest.fixture(autouse=True)
def forget_snapshot_folders(monkeypatch: pytest.MonkeyPatch):
    """A check's saves and loads publish their folders for the process's life; each check's are
    dropped after it, so no other check's grants meet them."""
    from wasi_perl import _grants

    monkeypatch.setattr(_grants, "_snapshot_folders", dict(_grants._snapshot_folders))
