"""Only the parser-owned outgoing repair spelling runs in its existing private owner."""
from pathlib import Path
import subprocess
import pytest

from tests.tools.test_browser_outgoing_continuity import outgoing as _outgoing
from tui_runtime import server


@pytest.fixture
def outgoing(tmp_path, monkeypatch):
    yield from _outgoing.__wrapped__(tmp_path, monkeypatch)


def test_exact_cli_repair_uses_fixed_management_in_selected_home_without_global_output(outgoing,monkeypatch,capsys):
    from hermes_cli import management
    from hermes_constants import get_hermes_home
    from hermes_cli.subcommands.secure_vault import OUTGOING_REPAIR_ARGUMENTS
    calls=[]
    monkeypatch.setattr(server,'_profile_home',lambda _profile:outgoing.home)
    def execute(request,home):
        calls.append((request,Path(home),get_hermes_home().resolve()))
        return {'saved':['browser-profile/outgoing-fixture.hbpa'],'retained':[]}
    monkeypatch.setattr(management,'execute',execute)
    result=server.handle_request({'id':'repair','method':'cli.exec','params':{
        'argv':list(OUTGOING_REPAIR_ARGUMENTS),'profile':'selected'}})['result']
    assert result['code']==0 and result['blocked'] is False
    assert 'Verified encrypted outgoing browser checkpoint' in result['output']
    assert calls==[({'op':'browser.outgoing.repair'},outgoing.home,outgoing.home)]
    assert capsys.readouterr().out==''
    spawned=[]
    def fake_run(argv,**kwargs):
        spawned.append(argv)
        return subprocess.CompletedProcess(argv,0,stdout='ordinary CLI',stderr='')
    monkeypatch.setattr(server.subprocess,'run',fake_run)
    for args in (['secure-vault','repair'],[*OUTGOING_REPAIR_ARGUMENTS,'--yes'],['config','show']):
        reply=server.handle_request({'id':'ordinary','method':'cli.exec','params':{'argv':args}})
        assert reply['result']['output']=='ordinary CLI'
    assert len(calls)==1 and len(spawned)==3
