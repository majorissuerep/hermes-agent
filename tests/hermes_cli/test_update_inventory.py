"""Frozen old-updater worklists remain passive JSON data, never runtime attachment."""
import json

from hermes_cli.update_inventory import RuntimeRecord, UpdatePlan


def test_serialized_historical_plan_roundtrips_without_losing_nested_evidence():
    payload = {
        "install_method": "git", "expected_sha": "a" * 40, "profiles": ["work"],
        "future_plan_field": "ignored", "runtimes": [{
            "kind": "gateway", "profile": "work", "pid": 4242,
            "detail": {"argv": ["hermes", "gateway", "run"], "old_evidence": [1, 2]},
            "future_runtime_field": "ignored",
        }],
    }
    plan = UpdatePlan.from_dict(json.loads(json.dumps(payload)))
    assert isinstance(plan.runtimes[0], RuntimeRecord)
    assert plan.expected_sha == "a" * 40
    assert plan.runtimes[0].detail == payload["runtimes"][0]["detail"]
    saved = plan.to_dict()
    assert saved["runtimes"][0]["pid"] == 4242
    assert "future_plan_field" not in saved
    assert "future_runtime_field" not in saved["runtimes"][0]
    assert UpdatePlan.from_dict(json.loads(json.dumps(saved))).to_dict() == saved
