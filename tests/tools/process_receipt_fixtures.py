"""Synthetic vault authority for unit contracts that persist process receipts."""

import pytest


@pytest.fixture(autouse=True)
def encrypted_process_home(_isolate_hermes_home):
    from hermes_constants import get_hermes_home
    from tests.e2e.vault_home import seal_fixture_home

    seal_fixture_home(get_hermes_home())
