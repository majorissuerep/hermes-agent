"""Growing encrypted conversations through every Responses route."""
import pytest

from scripts.e2e.provider_matrix import RESPONSES
from tests.scripts.e2e._provider_conversation import assert_growing_conversation, providers_for

pytestmark = pytest.mark.platforms("posix")


@pytest.mark.parametrize("provider", providers_for(RESPONSES))
@pytest.mark.parametrize("inject_faults", [False, True], ids=["growing", "rate-limit-and-summary-outage"])
def test_growing_conversation_compacts_and_resumes_encrypted_state(tmp_path, provider, inject_faults):
    assert_growing_conversation(tmp_path, provider, RESPONSES, inject_faults)
