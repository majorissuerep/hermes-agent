"""Install-age metrics remain bound to their encrypted owning profile."""

def test_milestone_install_age_is_the_subscriber_profile_not_the_relay_thread(tmp_path, monkeypatch):
    """Under multiplex the Relay thread carries no profile binding: a milestone reached in
    profile B must carry B's install age, not the launch profile's."""
    from hermes_security import sqlite as encrypted_sqlite
    from tests.e2e.vault_home import seal_fixture_home
    import time

    from hermes_cli.observability.shared_metrics import SharedMetricsStore
    from hermes_cli.observability.shared_metrics_subscriber import SharedMetricsSubscriber
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    launch, other = tmp_path / "A", tmp_path / "B"
    for home in (launch, other):
        home.mkdir()
        seal_fixture_home(home)
    with encrypted_sqlite.connect(other / "state.db") as con:
        con.execute("CREATE TABLE sessions (id TEXT, started_at REAL)")
        con.execute("INSERT INTO sessions VALUES ('s1', ?)", (time.time() - 120 * 86400,))
    monkeypatch.setenv("HERMES_HOME", str(launch))
    token = set_hermes_home_override(str(other))
    try:
        subscriber = SharedMetricsSubscriber(SharedMetricsStore(), "1.0")
    finally:
        reset_hermes_home_override(token)
    subscriber._record_milestones("hermes.setup.completed", {"provider": "nous", "surface": "cli"})
    ages = [c["dimensions"]["install_age_bucket"] for c in subscriber.store.counter_snapshot()
            if c["metric_name"] == "hermes.install.milestone"]
    assert ages == ["gte_90d"]
