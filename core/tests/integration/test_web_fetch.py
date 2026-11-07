import ssl
from types import SimpleNamespace

import pytest

from app.config import ToolSettings
from app.tools.contracts import ToolOperationError
from app.tools.web_fetch import PinnedHTTPSConnection, WebFetcher, public_address


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "192.168.1.2",
        "169.254.169.254",
        "0.0.0.0",
        "::1",
        "fc00::1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "224.0.0.1",
        "ff02::1",
    ],
)
def test_private_loopback_metadata_and_multicast_are_not_public(address):
    assert not public_address(address)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://other.com",
        "https://example.com:8443",
        "https://user:secret@example.com",
        "https://example.com.evil.test",
        "https://example.com/\r\nHost:private",
    ],
)
async def test_allowlist_and_scheme_are_checked_before_dns(tmp_path, url, monkeypatch):
    fetcher = WebFetcher(
        ToolSettings(
            _env_file=None,
            workspace_path=tmp_path,
            web_fetch_enabled=True,
            web_fetch_allowed_domains=["example.com"],
        )
    )

    async def forbidden(host):
        pytest.fail("Blocked URLs must never resolve DNS")

    monkeypatch.setattr(fetcher, "addresses", forbidden)
    with pytest.raises(ToolOperationError) as error:
        await fetcher.run(url)
    assert error.value.result.error.code == "NETWORK_BLOCKED"


async def test_redirects_revalidate_domains_and_dns_addresses(tmp_path, monkeypatch):
    fetcher = WebFetcher(
        ToolSettings(
            _env_file=None,
            workspace_path=tmp_path,
            web_fetch_enabled=True,
            web_fetch_allowed_domains=["example.com"],
        )
    )
    requests = []

    async def addresses(host):
        return ["93.184.216.34"]

    async def request(host, address, path, limit):
        requests.append((host, address, path))
        return 302, {"location": "https://127.0.0.1/private"}, b""

    monkeypatch.setattr(fetcher, "addresses", addresses)
    monkeypatch.setattr(fetcher, "request", request)
    with pytest.raises(ToolOperationError):
        await fetcher.run("https://example.com/start")
    assert requests == [("example.com", "93.184.216.34", "/start")]

    async def private(host):
        return ["127.0.0.1"]

    monkeypatch.setattr(fetcher, "addresses", private)
    with pytest.raises(ToolOperationError):
        await fetcher.run("https://example.com/start")
    assert len(requests) == 1


async def test_bounded_html_extraction_omits_scripts_and_records_redirects(tmp_path, monkeypatch):
    fetcher = WebFetcher(
        ToolSettings(
            _env_file=None,
            workspace_path=tmp_path,
            web_fetch_enabled=True,
            web_fetch_allowed_domains=["example.com"],
        )
    )

    async def addresses(host):
        return ["93.184.216.34"]

    async def request(host, address, path, limit):
        if path == "/start":
            return 302, {"location": "/page"}, b""
        return 200, {"content-type": "text/html"}, b"<p>Reference</p><script>secret code</script>"

    monkeypatch.setattr(fetcher, "addresses", addresses)
    monkeypatch.setattr(fetcher, "request", request)
    result = await fetcher.run("https://example.com/start")
    assert result["ok"] and result["data"]["text"] == "Reference"
    assert len(result["data"]["redirects"]) == 2
    disabled = WebFetcher(ToolSettings(_env_file=None, workspace_path=tmp_path))
    with pytest.raises(ToolOperationError) as error:
        await disabled.run("https://example.com")
    assert error.value.result.error.code == "UNAVAILABLE"


def test_https_dials_only_validated_address_and_verifies_original_hostname(monkeypatch):
    calls = []
    sock = SimpleNamespace(close=lambda: None)
    context = SimpleNamespace(
        verify_mode=ssl.CERT_REQUIRED,
        check_hostname=True,
        post_handshake_auth=True,
        wrap_socket=lambda raw, server_hostname: calls.append(server_hostname) or raw,
    )
    monkeypatch.setattr("app.tools.web_fetch.ssl.create_default_context", lambda: context)
    monkeypatch.setattr(
        "app.tools.web_fetch.socket.create_connection",
        lambda address, timeout: calls.append(address) or sock,
    )
    connection = PinnedHTTPSConnection("example.com", "93.184.216.34", 5)
    connection.connect()
    assert calls == [("93.184.216.34", 443), "example.com"]
    connection.cancelled.set()
    with pytest.raises(OSError, match="cancelled"):
        connection.connect()
    assert len(calls) == 2
