"""Optional provider metadata follows the active home, never the first import."""

from hermes_constants import reset_hermes_home_override, set_hermes_home_override


def test_provider_hostname_metadata_resolves_a_b_a_from_real_plugins(tmp_path):
    from agent.model_metadata import _infer_provider_from_url

    homes = [tmp_path / name for name in ("a", "b")]
    for home in homes:
        name = f"metadata-{home.name}"
        plugin = home / "plugins" / name
        plugin.mkdir(parents=True)
        (plugin / "plugin.yaml").write_text(f"name: {name}\nkind: model-provider\n", encoding="utf-8")
        (plugin / "__init__.py").write_text(
            "from providers import register_provider\nfrom providers.base import ProviderProfile\n"
            f"register_provider(ProviderProfile(name={name!r}, base_url='https://metadata.invalid/v1'))\n",
            encoding="utf-8",
        )
    for home in (homes[0], homes[1], homes[0]):
        token = set_hermes_home_override(home)
        try:
            assert _infer_provider_from_url("https://metadata.invalid/v1") == f"metadata-{home.name}"
        finally:
            reset_hermes_home_override(token)
