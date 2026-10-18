"""Native URL authority formatting, including IPv6 zone identifiers."""

class TestFormatUrlHost:

    def test_brackets_ipv6_literal_with_port_following(self):
        from hermes_cli.url_utils import format_url_host

        assert format_url_host("::1") == "[::1]"
        assert format_url_host("fe80::1") == "[fe80::1]"

    def test_preserves_ipv4_and_names(self):
        from hermes_cli.url_utils import format_url_host

        assert format_url_host("127.0.0.1") == "127.0.0.1"
        assert format_url_host("localhost") == "localhost"

    def test_already_bracketed_stays_bracketed(self):
        from hermes_cli.url_utils import format_url_host

        assert format_url_host("[::1]") == "[::1]"

    def test_escapes_ipv6_zone_identifier(self):
        from hermes_cli.url_utils import format_url_host

        assert format_url_host("fe80::1%eth0") == "[fe80::1%25eth0]"
        assert format_url_host("[fe80::1%eth0]") == "[fe80::1%25eth0]"
