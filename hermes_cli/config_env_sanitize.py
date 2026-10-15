"""Normalize credential-file formatting during configuration migration."""
import io


def sanitize_env_file() -> int:
    """Normalize the active home's .env; return the number of changed lines."""
    from hermes_cli import config

    env_path = config.get_env_path()
    if not env_path.exists():
        return 0
    with config._env_write_lock(env_path):
        # The writer seals this file; the reader must authenticate/decrypt it
        # before normalization, including when an updater invokes migration.
        text = config._hermes_io().read_text(env_path, purpose="env", encoding="utf-8-sig")
        original_lines = io.StringIO(text or "", newline=None).readlines()
        sanitized = config._sanitize_env_lines(original_lines)
        if sanitized == original_lines:
            return 0
        fixes = abs(len(sanitized) - len(original_lines)) or sum(
            1 for a, b in zip(original_lines, sanitized) if a != b)
        config._write_env_lines(env_path, sanitized, preserve_mode=False)
    config.invalidate_env_cache()
    return fixes
