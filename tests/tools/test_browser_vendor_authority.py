"""Browser vendor environments never carry the local Hermes unlock capability.

Uses real synthetic session authority and the production environment builders, without launching
any browser, SDK or application. Trusted Hermes child environments retain their existing grant.
"""
import pytest

pytestmark = pytest.mark.platforms('linux', 'macos')


@pytest.mark.parametrize('consumer',['native-browser','browser-use'])
def test_vendor_scrub_runs_after_scoped_authority_is_reapplied(tmp_path, monkeypatch, consumer):
    from agent.session_context import scoped_current_session_id
    from hermes_security import vault
    from hermes_security.session_authority import ENV_NAME,start_session_authority
    from tools import browser_tool, browser_use_cli
    from tools.environments.local import served_profile_child_env
    from tools.environments.local_env_policy import LOCAL_VAULT_AUTHORITY_ENV

    home,user,runtime=(tmp_path/name for name in ('owner','user','runtime'))
    for path in (home,user,runtime):path.mkdir(mode=0o700)
    monkeypatch.setenv('HOME',str(user))
    monkeypatch.setenv('HERMES_HOME',str(home))
    monkeypatch.setenv('HERMES_RUNTIME_DIR',str(runtime))
    monkeypatch.setattr(vault,'_SCRYPT_N',1024)
    monkeypatch.setattr(browser_tool,'_socket_safe_tmpdir',lambda:str(runtime))
    vault.init_vault(home,'synthetic browser vendor boundary')
    vault.unlock(home,'synthetic browser vendor boundary')
    authority=start_session_authority(home,'browser-vendor-real-source')
    try:
        monkeypatch.setenv('HERMES_SESSION_ID',authority.session_id)
        monkeypatch.setenv(ENV_NAME,authority.environment()[ENV_NAME])
        monkeypatch.setenv('BROWSERBASE_API_KEY','synthetic selected browser credential')
        with scoped_current_session_id(authority.session_id):
            trusted=served_profile_child_env(inherit_credentials=False)
            trusted_grant_present=ENV_NAME in trusted
            assert trusted_grant_present
            vendor=(browser_tool._build_browser_env() if consumer=='native-browser'
                    else browser_use_cli._base_subprocess_env())
            leaked_count=sum(key.upper() in LOCAL_VAULT_AUTHORITY_ENV for key in vendor)
            assert leaked_count==0
            selected_browser_credential_preserved=vendor.get('BROWSERBASE_API_KEY')=='synthetic selected browser credential'
            assert selected_browser_credential_preserved
            assert authority.is_live() and vault.is_unlocked(home)
    finally:
        authority.close()
        vault.clear_vault_cache()
