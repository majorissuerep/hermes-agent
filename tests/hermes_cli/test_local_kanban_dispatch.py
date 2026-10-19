"""The terminal work queue runs locally and subscribes only local TUI sessions."""

import argparse

import pytest

from hermes_cli import kanban as cli
from hermes_cli import kanban_db as db
from hermes_cli import kanban_db_connect as store
from hermes_cli import kanban_db_notify as notifications


def parser():
    result = argparse.ArgumentParser()
    cli.build_parser(result.add_subparsers(dest="command"))
    return result


def test_removed_daemon_is_not_an_accepted_launch_mode():
    with pytest.raises(SystemExit) as error:
        parser().parse_args(["kanban", "daemon"])
    assert error.value.code == 2


def test_subscribe_rejects_remote_management_destination():
    with pytest.raises(SystemExit) as error:
        parser().parse_args([
            "kanban", "notify-subscribe", "task", "--platform", "telegram", "--chat-id", "remote",
        ])
    assert error.value.code == 2


def test_subscribe_records_local_session_without_remote_routing():
    with store.connect_closing() as connection:
        task = db.create_task(connection, title="Local work", assignee="worker")
    args = parser().parse_args([
        "kanban", "notify-subscribe", task, "--session-id", "owned-tui", "--delivery-mode", "notify+wake",
    ])
    assert cli.kanban_command(args) == 0
    with store.connect_closing() as connection:
        subscription, = notifications.list_notify_subs(connection, task)
    assert (subscription["platform"], subscription["chat_id"], subscription["delivery_mode"]) == (
        "tui", "owned-tui", "notify+wake",
    )
    assert not subscription["thread_id"]
    assert not subscription["delivery_metadata"]
