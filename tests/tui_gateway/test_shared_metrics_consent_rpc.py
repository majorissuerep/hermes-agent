"""Desktop clients respect the fork telemetry policy and the selected profile."""

from __future__ import annotations

from pathlib import Path

import hermes_yaml as yaml

import tui_gateway.server as server


def _bind_homes(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    launch, worker = tmp_path / "launch", tmp_path / "profiles" / "code"
    for home in (launch, worker):
        home.mkdir(parents=True)
        (home / "config.yaml").write_text(yaml.safe_dump({"model": {"provider": "nous"}}), encoding="utf-8")
    monkeypatch.setattr(server, "_hermes_home", launch)
    monkeypatch.setenv("HERMES_HOME", str(launch))
    monkeypatch.setattr(server, "_profile_home", lambda name: worker if (name or "").strip() == "code" else None)
    server._cfg_cache = server._cfg_sig = server._cfg_path = None
    return launch, worker


def _call(method: str, params: dict) -> dict:
    return server._methods[method]("rid", params)["result"]


def _shared_metrics(home: Path) -> dict:
    cfg = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8")) or {}
    return (cfg.get("telemetry") or {}).get("shared_metrics") or {}


def test_telemetry_opt_in_is_disabled_in_the_requested_profile(tmp_path, monkeypatch):
    launch, worker = _bind_homes(monkeypatch, tmp_path)
    import hermes_cli.observability.shared_metrics_events as events

    calls = []
    monkeypatch.setattr(events, "record_setup_completed", lambda **kw: calls.append(kw))
    assert _call("shared_metrics.set", {
        "profile": "code", "enabled": True, "send": True, "first_run": True,
    }) == {"enabled": False, "send": False, "decided": True}
    assert _shared_metrics(worker) == {"enabled": False, "send": False}
    assert _shared_metrics(launch) == {}
    assert calls == []
    assert not (launch / "telemetry").exists()
    assert not (worker / "telemetry").exists()


def test_status_reports_fork_policy_across_profiles_with_legacy_opt_ins(tmp_path, monkeypatch):
    launch, worker = _bind_homes(monkeypatch, tmp_path)
    (worker / "config.yaml").write_text(
        yaml.safe_dump({"telemetry": {"shared_metrics": {"enabled": True, "send": True}}}),
        encoding="utf-8",
    )
    before = {home: (home / "config.yaml").read_bytes() for home in (launch, worker)}
    for params in ({}, {"profile": "code"}, {}):
        assert _call("shared_metrics.status", params) == {
            "enabled": False, "send": False, "decided": True,
        }
    assert {home: (home / "config.yaml").read_bytes() for home in before} == before
