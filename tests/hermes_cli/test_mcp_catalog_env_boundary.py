"""Native outgoing MCP installation validates inputs before writes and keeps .env secrets-only."""
import os
from pathlib import Path
import pytest
import hermes_yaml as yaml
from tools.connectors.mcp import _CatalogBackend

@pytest.fixture
def catalog_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _isolate_hermes_home):
    """Install one synthetic API-key catalog entry in the isolated test home."""
    from hermes_constants import get_hermes_home
    from hermes_cli.config import invalidate_env_cache

    catalog = tmp_path / "optional-mcps"
    entry_dir = catalog / "demo"
    entry_dir.mkdir(parents=True)
    (entry_dir / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "manifest_version": 1,
                "name": "demo",
                "description": "Synthetic dashboard boundary fixture",
                "source": "https://example.test/demo",
                "transport": {
                    "type": "stdio",
                    "command": "demo-mcp",
                },
                "auth": {
                    "type": "api_key",
                    "env": [
                        {
                            "name": "DEMO_API_KEY",
                            "prompt": "Demo API key",
                            "secret": True,
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_OPTIONAL_MCPS", str(catalog))
    invalidate_env_cache()
    return get_hermes_home()



def test_undeclared_key_is_rejected_before_probe_or_write(catalog_env, monkeypatch):
    probed=[]
    monkeypatch.setattr('hermes_cli.mcp_config._probe_single_server', lambda *a, **k: probed.append(True))
    with pytest.raises(ValueError, match='does not declare'):
        _CatalogBackend().install('demo', {'DEMO_API_KEY':'test-secret', 'UNRELATED_SETTING':'no'})
    assert probed == []
    assert not (catalog_env / '.env').exists()


def test_reserved_control_key_is_refused_even_when_declared(catalog_env, monkeypatch):
    manifest = Path(os.environ['HERMES_OPTIONAL_MCPS']) / 'demo' / 'manifest.yaml'
    raw = yaml.safe_load(manifest.read_text())
    raw['auth']['env'].append({'name':'HERMES_YOLO_MODE','secret':False})
    manifest.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match='denylist'):
        _CatalogBackend().install('demo', {'HERMES_YOLO_MODE':'1'})
    assert not (catalog_env / '.env').exists()


def test_native_install_writes_secret_but_inlines_nonsecret_configuration(catalog_env, monkeypatch):
    manifest = Path(os.environ['HERMES_OPTIONAL_MCPS']) / 'demo' / 'manifest.yaml'
    raw=yaml.safe_load(manifest.read_text())
    raw['transport']={'type':'http','url':'${DEMO_BASE_URL}'}
    raw['auth']['env']=[{'name':'MCP_DEMO_API_KEY','secret':True}, {'name':'DEMO_BASE_URL','secret':False}]
    manifest.write_text(yaml.safe_dump(raw))
    monkeypatch.setattr('hermes_cli.mcp_config._probe_single_server', lambda *a, **k:[('demo_tool','')])
    assert _CatalogBackend().install('demo',{'MCP_DEMO_API_KEY':'fixture-secret','DEMO_BASE_URL':'https://demo.invalid'}) == ['demo_tool']
    text=(catalog_env / '.env').read_text()
    assert 'MCP_DEMO_API_KEY=fixture-secret' in text
    assert 'DEMO_BASE_URL' not in text and 'demo.invalid' not in text
    from hermes_cli.config import load_config
    assert load_config()['mcp_servers']['demo']['url'] == 'https://demo.invalid'
