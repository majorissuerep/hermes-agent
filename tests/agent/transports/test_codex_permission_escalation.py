"""Codex permission escalations decline with the protocol's empty granted subset."""
from unittest.mock import Mock

import pytest

from agent.transports.codex_app_server_session import CodexAppServerSession, _ServerRequestRouting


@pytest.mark.parametrize("autoapprove", [False, True])
def test_permission_request_never_grants_network_or_filesystem_escalation(autoapprove, tmp_path):
    approval = Mock(side_effect=AssertionError("permission escalation must not use shell approval"))
    session = CodexAppServerSession(
        cwd=str(tmp_path), approval_callback=approval,
        request_routing=_ServerRequestRouting(
            auto_approve_exec=autoapprove, auto_approve_apply_patch=autoapprove))
    client = Mock()
    session._client = client
    session._handle_server_request({"id": "permission-request-17", "method": "item/permissions/requestApproval",
                                    "params": {"permissions": {
                                        "network": {"enabled": True},
                                        "fileSystem": {"write": [str(tmp_path)]}}}})
    client.respond.assert_called_once_with("permission-request-17", {"permissions": {}})
    client.respond_error.assert_not_called()
    approval.assert_not_called()
