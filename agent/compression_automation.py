"""Carry automation at a compression boundary without opening worker storage."""


def carry_compression_automation(agent, old_session_id):
    from agent.conversation_compression import _swallow
    with _swallow('Could not migrate goal on compression: %s'):
        from hermes_cli.goals import migrate_goal_to_session
        migrate_goal_to_session(old_session_id, agent.session_id, reason='compression')
    with _swallow('Could not migrate heartbeat on compression: %s'):
        from hermes_cli.heartbeat import migrate_heartbeat_to_session
        migrate_heartbeat_to_session(old_session_id, agent.session_id)
    with _swallow('Could not migrate loop on compression: %s'):
        from hermes_cli.loops import migrate_loop_to_session
        migrate_loop_to_session(old_session_id, agent.session_id, reason='compression')
