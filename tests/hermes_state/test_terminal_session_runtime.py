"""Native resume route decoding keeps historical encrypted-session compatibility."""
import json
from hermes_state import SessionDB


def test_native_route_decoder_prefers_current_route_without_losing_old_history():
    current = {'provider':'new-provider','base_url':'https://native.invalid/v1','api_mode':'chat_completions'}
    old = {'provider':'old-provider','base_url':'https://old.invalid/v1'}
    assert SessionDB.session_runtime({'model_config':json.dumps({'runtime':current,'gateway_runtime':old})}) == current
    assert SessionDB.session_runtime({'model_config':json.dumps({'gateway_runtime':old})}) == old
    assert SessionDB.session_runtime({'model_config':'not-json','billing_provider':'openai'}) == {'provider':'openai'}
