"""A `$HERMES_HOME` provider plugin re-registering a bundled name reaches the runtime (#48450).

``register_provider()`` is last-writer-wins for the profile, and the docs promise that dropping
``plugins/model-providers/<bundled-name>/`` points that provider at another endpoint. The runtime
reads ``hermes_cli.auth.PROVIDER_REGISTRY`` though, so the mirror has to carry the override across.
Each case runs in a fresh interpreter: real discovery, real auth import, no process-global leakage.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

_PROBE = """
import json, os
from providers import list_providers
from hermes_cli.auth import PROVIDER_REGISTRY
from hermes_cli.runtime_provider import resolve_runtime_provider
list_providers()
row = PROVIDER_REGISTRY["ai-gateway"]
print(json.dumps({
    "runtime_base_url": resolve_runtime_provider(requested="ai-gateway")["base_url"],
    "api_key_env_vars": list(row.api_key_env_vars), "base_url_env_var": row.base_url_env_var,
    "xai_base_url": PROVIDER_REGISTRY["xai"].inference_base_url}))
"""


def _run(tmp_path: Path, plugin_source: str) -> dict:
    home = tmp_path / "home"
    plugin_dir = home / "plugins" / "model-providers" / "ai-gateway"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "__init__.py").write_text(plugin_source, encoding="utf-8")
    env = {**os.environ, "HERMES_HOME": str(home), "PYTHONPATH": str(REPO), "AI_GATEWAY_API_KEY": "sk-fixture"}
    env.pop("AI_GATEWAY_BASE_URL", None)
    proc = subprocess.run([sys.executable, "-c", _PROBE], env=env, capture_output=True, text=True, timeout=120,
                          cwd=str(REPO), check=False)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_user_plugin_endpoint_and_env_vars_reach_the_runtime(tmp_path):
    result = _run(tmp_path, (
        "from providers import register_provider\n"
        "from providers.base import ProviderProfile\n"
        "register_provider(ProviderProfile(name='ai-gateway', aliases=('regional-gateway',), auth_type='api_key',\n"
        "    env_vars=('AI_GATEWAY_API_KEY', 'AI_GATEWAY_REGIONAL_BASE_URL'),\n"
        "    base_url='https://ai-gateway-override.invalid/v1'))\n"))
    assert result["runtime_base_url"] == "https://ai-gateway-override.invalid/v1"
    assert result["base_url_env_var"] == "AI_GATEWAY_REGIONAL_BASE_URL"
    assert result["api_key_env_vars"] == ["AI_GATEWAY_API_KEY"]


def test_user_plugin_declaring_no_endpoint_keeps_the_builtin_row(tmp_path):
    from hermes_cli.auth import PROVIDER_REGISTRY

    result = _run(tmp_path, (
        "from providers import register_provider\n"
        "from providers.base import ProviderProfile\n"
        "register_provider(ProviderProfile(name='ai-gateway', auth_type='api_key', env_vars=('AI_GATEWAY_API_KEY',)))\n"))
    assert result["runtime_base_url"] == PROVIDER_REGISTRY["ai-gateway"].inference_base_url
    assert result["base_url_env_var"] == "AI_GATEWAY_BASE_URL"
    assert result["xai_base_url"] == PROVIDER_REGISTRY["xai"].inference_base_url
