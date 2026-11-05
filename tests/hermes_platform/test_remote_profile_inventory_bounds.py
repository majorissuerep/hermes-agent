"""Native inventory bounds must apply before filesystem names are materialized."""
from contextlib import contextmanager
import hashlib
import json
import os
import stat

import pytest

from hermes_platform.remote_profile_inventory import program

pytestmark = pytest.mark.platforms("linux")


def test_small_native_name_limit_stops_before_materializing_whole_directory(tmp_path, monkeypatch):
    root = tmp_path / "names"
    root.mkdir()
    for index in range(20):
        (root / str(index)).touch()
    namespace = {"os": os, "stat": stat, "hashlib": hashlib, "json": json}
    exec(program(), namespace)
    namespace["INVENTORY_ENTRIES"] = 2
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    observed = []
    original_listdir, original_scandir = os.listdir, os.scandir

    def listdir(target):
        names = original_listdir(target)
        if target == descriptor:
            observed.extend(names)
        return names

    @contextmanager
    def scandir(target):
        with original_scandir(target) as listing:
            def entries():
                for entry in listing:
                    if target == descriptor:
                        observed.append(entry.name)
                    yield entry
            yield entries()

    monkeypatch.setattr(os, "listdir", listdir)
    monkeypatch.setattr(os, "scandir", scandir)
    try:
        with pytest.raises(ValueError, match="bound"):
            namespace["native_inventory"](descriptor)
        assert 0 < len(observed) <= 3  # At most the admitted names and one rejecting observation.
        assert len(original_listdir(descriptor)) == 20
    finally:
        os.close(descriptor)
