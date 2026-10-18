"""Validation of passive historical ledger values."""
import json
from hermes_state.errors import RuntimeStoreError

def _text(value):
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise RuntimeStoreError('invalid_params')
    return value

def _json(value):
    if not isinstance(value, dict):
        raise RuntimeStoreError('invalid_params')
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise RuntimeStoreError('invalid_params') from exc
