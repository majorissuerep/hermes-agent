"""One existing worker.repair consumer for retained Lightpanda state, no new command/job."""
from pathlib import Path
import os

from hermes_cli import browser_worker_store as store
from hermes_security.errors import VaultError, VaultLockedError
from hermes_security.session_custody import SessionCustody
from hermes_security.operation_custody import guarded
from tools.browser_lightpanda_state import checkpoint_idle, legacy_path


def _ended(server):
    server.lease.resource.custody.check()
    try:
        server.lease.source_operation.check()
    except VaultLockedError:
        return True
    return False


def _settle_resident(home, report):
    from tools import browser_lightpanda as lp

    with lp._servers_lock:
        servers = [server for server in lp._servers.values() if server.lease.home == home]
    for server in servers:
        try:
            if server.is_alive() and (not _ended(server) or server.shared_with_other(server.lease.source)):
                report['retained'].append(f'{server.lease.resource.path}: a Lightpanda source remains live; close its session first')
                continue
            operation = SessionCustody.capture(home, resource=server.lease.resource.custody)
            lp.stop_lightpanda(server.session_name, home=home, expected=server.lease.token, operation=operation)
        except (VaultError, OSError, RuntimeError, ValueError) as error:
            report['retained'].append(f'{server.lease.resource.path}: {error}')


def repair_owned(home):
    """Fresh selected Source; unknown/live/foreign owners are never stopped or overwritten."""
    home = Path(home).resolve()
    operation = guarded(SessionCustody.capture(home))
    operation.check()
    report = {'saved':[], 'retained':[]}
    from tools import browser_lightpanda as lp
    from hermes_security import browser_worker_archive as archive, browser_worker_pending as pending

    with lp._servers_lock:
        resident = any(server.lease.home == home for server in lp._servers.values())
    digest = archive.identity_digest(operation, {'kind':'lightpanda-http-cache'})
    if not resident and not legacy_path(home).exists() and not legacy_path(home).is_symlink() and not (
            home/pending.relative(digest)).exists():
        return report  # No LP work: do not provision a RAM resource or affect other browser producers.
    _settle_resident(home, report)
    if report['retained']:
        return report
    reference_path = home/pending.relative(digest)
    location = str(legacy_path(home)) if os.path.lexists(legacy_path(home)) else str(reference_path)
    try:
        document = pending.read_optional(home, digest, custody=operation)
        if document is not None:
            location += f"; original RAM {document['path']}"
        checkpoint = checkpoint_idle(home, operation)
        if checkpoint is not None:
            report['saved'].append(checkpoint)
        if legacy_path(home).exists() or legacy_path(home).is_symlink():
            from tools.browser_lightpanda_legacy import import_literal

            imported = import_literal(home)
            report['saved'].extend([imported['archive'], imported['recovery']])
    except (VaultError, OSError, RuntimeError, ValueError) as error:
        report['retained'].append(f'{location}: {error}')
    return report
