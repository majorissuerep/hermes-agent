"""Source updater channel read/retry and authority diagnostics."""
import pytest

@pytest.mark.parametrize("name", ["X", "../x", "two--parts", "a/b", "a%2fb", "a_1", "con", "nul", "x-", "x" * 33, " a", "a\n"])
def test_names_are_validated_without_normalizing(name):
    from hermes_cli.release_channels import ChannelError, validate_name
    with pytest.raises(ChannelError):
        validate_name(name)


def test_update_reads_retry_transient_http_and_honor_retry_after(monkeypatch):
    from email.message import Message
    from urllib.error import HTTPError
    from hermes_cli.release_channels import ChannelReader, retrying_reads

    url = "http://127.0.0.1:12345/releases/fixture.json"
    headers = Message()
    headers["Retry-After"] = "7"
    attempts = []
    waits = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def geturl(self):
            return url

        def read(self, _limit):
            return b"fixture"

    def opener(request, timeout):
        assert timeout == 30
        attempts.append(request.full_url)
        if len(attempts) == 1:
            raise HTTPError(url, 503, "unavailable", headers, None)
        return Response()

    monkeypatch.setattr("pm.network.time.sleep", waits.append)
    reader = ChannelReader("http://127.0.0.1:12345", opener=opener)
    with retrying_reads():
        assert reader.read_bytes("releases/fixture.json") == b"fixture"
    assert attempts == [url, url]
    assert waits == [7.0]


def test_passive_reads_and_missing_objects_make_one_attempt(monkeypatch):
    """Offline looks transient (ENETUNREACH); a passive check must not back off on it."""
    import errno
    from email.message import Message
    from urllib.error import HTTPError, URLError
    from hermes_cli.release_channels import ChannelError, ChannelNotFound, ChannelReader, retrying_reads

    waits = []
    monkeypatch.setattr("pm.network.time.sleep", waits.append)

    def reader(fault, calls):
        def opener(request, timeout):
            calls.append(request.full_url)
            raise fault
        return ChannelReader("https://releases.example", opener=opener)

    calls = []
    offline = URLError(OSError(errno.ENETUNREACH, "Network is unreachable"))
    with pytest.raises(ChannelError, match="unavailable"):
        reader(offline, calls).read_bytes("releases/channels/main.json")
    assert len(calls) == 1

    calls.clear()
    missing = HTTPError("https://releases.example/x", 404, "missing", Message(), None)
    with retrying_reads(), pytest.raises(ChannelNotFound):
        reader(missing, calls).read_bytes("releases/channels/stable.json")
    assert len(calls) == 1
    assert waits == []


@pytest.mark.parametrize(("base", "headers", "blocked"), [
    ("https://hermes-assets.nousresearch.com", {"Server": "cloudflare", "Content-Type": "text/html"}, True),
    ("https://hermes-assets.nousresearch.com", {"Server": "cloudflare", "cf-cache-status": "DYNAMIC"}, False),
    ("https://other-r2.example", {"Server": "cloudflare", "Content-Type": "text/html"}, False),
])
def test_cdn_firewall_403_names_the_network_not_the_install(base, headers, blocked):
    """#128295: a Cloudflare block that never reached our bucket must not read as a bare HTTP 403."""
    from email.message import Message
    from urllib.error import HTTPError
    from hermes_cli.release_channels import ChannelError, ChannelReader

    hdrs = Message()
    for key, value in headers.items():
        hdrs[key] = value

    def opener(request, timeout):
        raise HTTPError(request.full_url, 403, "Forbidden", hdrs, None)

    with pytest.raises(ChannelError) as raised:
        ChannelReader(base, opener=opener).read_bytes("releases/channels/pm-preview.json")
    assert ("CDN firewall (Cloudflare) refused this network" in str(raised.value)) is blocked
    assert str(raised.value).startswith("Channel read unavailable: HTTP 403")
