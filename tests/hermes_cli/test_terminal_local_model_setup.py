"""Local model setup remains a CLI operation after HTTP UI removal."""
from hermes_cli.local_runtime.errors import LocalModelError
from hermes_cli.model_setup_flows_local import _start


def test_model_setup_refusal_is_actionable_without_http_framework(capsys):
    def refuse():
        raise LocalModelError('Model files are not installed')
    assert _start(None, refuse) is None
    assert 'Model files are not installed' in capsys.readouterr().out
