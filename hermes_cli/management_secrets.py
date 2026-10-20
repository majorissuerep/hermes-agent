"""The same encrypted credential lifecycle used by native config commands."""


def secret_operation(request, value=None):
    from hermes_cli import config
    from hermes_cli.config_env_routing import remove_env_setting, save_env_setting
    from hermes_cli.credential_lifecycle import remove_provider_env_credential, save_provider_env_credential
    from hermes_cli.management_config import ManagementError

    name = request.get("name")
    if not isinstance(name, str):
        raise ManagementError("invalid_request", "secret name must be an environment variable identifier")
    config.validate_env_var_name_for_write(name)
    config.require_env_writable(name, "set" if request["op"] == "secret.set" else "remove")
    registered = config._is_env_config_key(name)
    if request["op"] == "secret.set":
        writer = save_provider_env_credential if registered else save_env_setting
        writer(name, value)
        return {"changed": True, "effect": "next_session"}
    changed = remove_provider_env_credential(name)["found"] if registered else remove_env_setting(name)
    return {"changed": changed, "effect": "next_session"}
