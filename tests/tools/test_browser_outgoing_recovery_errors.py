"""Malformed transfer remains a typed no-effect recovery report, using real crypto/helper.

The existing fixture labels synthetic backend/browser/RAM seams; no vendor or CLI runs.
"""
import pytest

pytest_plugins = ["tests.tools.test_browser_outgoing_continuity",
                  "tests.tools.test_browser_outgoing_inventory_refusal"]
pytestmark = pytest.mark.platforms("linux")


def test_malformed_transfer_retains_recovery_report_and_every_original_copy(pending, monkeypatch):
    from tests.tools.test_browser_outgoing_inventory_refusal import _snapshot
    from tools import browser_outgoing_recovery as recovery, browser_outgoing_state as state
    from tools import browser_outgoing_transfer as transfer

    _outgoing, sibling, profile, lease, document = pending
    paths = [lease.home / lease.relative, lease.home / state.relative(lease.digest),
             lease.home / document["inventory"]["relative"]]
    before_ciphers = [path.read_bytes() for path in paths]
    before_profile = _snapshot(profile)
    original = transfer.download_raw

    def malformed(*args, **kwargs):
        path, digest, bound = original(*args, **kwargs)
        raw = path.read_bytes()
        path.write_bytes(b"broken-header" + raw[13:])
        return path, digest, bound

    monkeypatch.setattr(transfer, "download_raw", malformed)
    error, report = None, None
    try:
        report = recovery.recover(lease.home)
    except Exception as caught:
        error = caught
    assert [path.read_bytes() for path in paths] == before_ciphers
    assert _snapshot(profile) == before_profile
    assert sibling.is_live()
    assert error is None, f"Unclassified recovery error escaped: {type(error).__name__}"
    assert report["saved"] == [] and report["retained"]
