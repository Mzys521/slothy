"""Allowlisted HTTPS text retrieval with DNS-pinned connections and verified TLS. /
仅访问允许列表 HTTPS 域名，使用 DNS 固定连接与校验 TLS 的有界文本读取。"""

import asyncio
import http.client
import ipaddress
import socket
import ssl
from html.parser import HTMLParser
from threading import Event
from urllib.parse import urljoin, urlsplit, urlunsplit

from app.config import ToolSettings
from app.tools.contracts import ToolOperationError, ToolResult


def normalize_host(host: str) -> str:
    """
    Normalize an allowlist hostname for exact comparison. / 规范化允许列表主机名以便精确比较。

    Args:
        host: Hostname from a URL or allowlist entry. / URL 或允许列表中的主机名。

    Returns:
        Lowercase ASCII IDNA hostname without a trailing dot. / 去掉末尾点、转为小写 ASCII
        IDNA 的主机名。

    Raises:
        ToolOperationError: If the hostname cannot be IDNA-encoded. / 主机名无法进行 IDNA
        编码时抛出。
    """
    try:
        return host.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise ToolOperationError("NETWORK_BLOCKED", "Invalid hostname.") from error


def public_address(value: str) -> bool:
    """Report whether an address is globally routable. / 判断地址是否为可全球路由的公有地址。

    Args:
        value: IPv4 or IPv6 address string. / IPv4 或 IPv6 地址字符串。

    Returns:
        True only for global, non-multicast addresses and mapped IPv4 equivalents. /
        仅对全局且非多播地址及其映射 IPv4 返回 True。
    """
    address = ipaddress.ip_address(value)
    mapped = getattr(address, "ipv4_mapped", None)
    return (
        address.is_global
        and not address.is_multicast
        and (mapped is None or (mapped.is_global and not mapped.is_multicast))
    )


class TextExtractor(HTMLParser):
    """
    Collect visible text from HTML while skipping hidden elements. / 从 HTML
    中收集可见文本并跳过隐藏元素。
    """

    def __init__(self):
        """Initialize the HTML text accumulator. / 初始化 HTML 文本累积器。"""
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        """
        Track hidden blocks and add line breaks for block tags. /
        跟踪隐藏块并为块级标签添加换行。

        Args:
            tag: Lowercase HTML start tag name. / 小写 HTML 起始标签名。
            attrs: Tag attributes supplied by HTMLParser. / HTMLParser 提供的标签属性。
        """
        if tag in {"script", "style", "noscript", "template"}:
            self.hidden += 1
        elif not self.hidden and tag in {"p", "div", "br", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        """Leave a previously opened hidden block. / 离开之前打开的隐藏块。

        Args:
            tag: Lowercase HTML end tag name. / 小写 HTML 结束标签名。
        """
        if tag in {"script", "style", "noscript", "template"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        """Append visible character data to the extracted text. / 将可见字符数据追加到提取文本。

        Args:
            data: Decoded character data between tags. / 标签之间已解码的字符数据。
        """
        if not self.hidden:
            self.parts.append(data)


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """
    HTTPS connection that dials one pre-validated numeric address. /
    仅连接预先校验的固定数字地址的 HTTPS 连接。

    TLS still verifies the original hostname; the connection never re-resolves DNS. /
    TLS 仍校验原主机名，连接不会再次解析 DNS。
    """

    def __init__(self, host: str, address: str, timeout: float):
        """
        Create a pinned HTTPS connection for one validated address. / 为已校验地址创建固定
        HTTPS 连接。

        Args:
            host: Original hostname used for TLS server-name verification. / 用于 TLS
            服务名校验的原主机名。
            address: Numeric IP address resolved and validated by the caller. /
            调用方解析并校验的数字 IP 地址。
            timeout: Socket timeout in seconds. / 套接字超时秒数。
        """
        super().__init__(host, port=443, timeout=timeout, context=ssl.create_default_context())
        self.address = address
        self.cancelled = Event()

    def connect(self):
        """
        Open TCP to the pinned address, then wrap it in verified TLS. / 连接固定地址的
        TCP，再用校验 TLS 包裹。

        Raises:
            OSError: If the TCP connection or TLS handshake fails. / TCP 连接或 TLS
            握手失败时抛出。
        """
        # English: The numeric address is resolved exactly once by the validated caller.
        # 中文：数字地址只由已校验的调用方解析一次。
        if self.cancelled.is_set():
            raise OSError("Connection cancelled")
        raw = socket.create_connection((self.address, 443), self.timeout)
        try:
            if self.cancelled.is_set():
                raise OSError("Connection cancelled")
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
            if self.cancelled.is_set():
                self.close()
                raise OSError("Connection cancelled")
        except BaseException:
            raw.close()
            raise


class WebFetcher:
    """Fetch bounded text from allowlisted HTTPS URLs. / 从允许列表 HTTPS URL 获取有界文本。

    Scheme, port, credentials and domain are checked, every resolved address must be
    public, and redirects are followed only while they stay on the allowlist. /
    校验协议、端口、凭据与域名，所有解析地址必须为公有地址，重定向仅在允许列表内跟随。
    """

    def __init__(self, settings: ToolSettings):
        """
        Bind fetcher limits and allowlist to tool settings. /
        将抓取限制与允许列表绑定到工具配置。

        Args:
            settings: Allowlist, byte cap, timeout and output caps. /
            允许列表、字节上限、超时与输出上限。
        """
        self.settings = settings

    def validate_url(self, url: str):
        """Validate an HTTPS URL against credentials, port and allowlist rules. /
        校验 HTTPS URL 的凭据、端口与允许列表规则。

        Args:
            url: Absolute HTTPS URL to validate. / 待校验的绝对 HTTPS URL。

        Returns:
            Tuple of the parsed URL and normalized hostname. / 解析后 URL 与规范化主机名的元组。

        Raises:
            ToolOperationError: If the URL is not an allowlisted HTTPS URL on port 443. /
                URL 不是 443 端口上允许的 HTTPS 地址时抛出。
        """
        try:
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port not in (None, 443)
                or any(ord(char) < 33 for char in url)
                or "\\" in url
            ):
                raise ValueError("HTTPS on port 443 is required")
            host = normalize_host(parsed.hostname)
        except (ValueError, UnicodeError) as error:
            raise ToolOperationError(
                "NETWORK_BLOCKED", "Use an HTTPS URL without credentials."
            ) from error
        allowed = {normalize_host(item) for item in self.settings.web_fetch_allowed_domains}
        if host not in allowed:
            raise ToolOperationError("NETWORK_BLOCKED", "Domain is not on the HTTPS allowlist.")
        return parsed, host

    async def addresses(self, host: str) -> list[str]:
        """
        Resolve a hostname and require every address to be public. /
        解析主机名并要求所有地址均为公有地址。

        Args:
            host: Normalized hostname from the validated URL. / 已校验 URL 的规范化主机名。

        Returns:
            Unique validated numeric addresses. / 去重后的已校验数字地址列表。

        Raises:
            ToolOperationError: If DNS fails or any resolved address is not public. /
                DNS 失败或任一地址非公有地址时抛出。
        """
        try:
            records = await asyncio.get_running_loop().getaddrinfo(
                host, 443, type=socket.SOCK_STREAM
            )
        except OSError as error:
            raise ToolOperationError(
                "EXEC_FAILED", "DNS resolution failed.", retryable=True
            ) from error
        addresses = list(dict.fromkeys(record[4][0] for record in records))
        if not addresses or any(not public_address(address) for address in addresses):
            raise ToolOperationError(
                "NETWORK_BLOCKED", "Only public network addresses are allowed."
            )
        return addresses

    async def request(self, host: str, address: str, path: str, limit: int):
        """
        Perform one blocking HTTPS GET outside the event loop. / 在事件循环外执行一次阻塞式
        HTTPS GET。

        Args:
            host: Original hostname for the TLS server name. / TLS 服务名使用的原主机名。
            address: Pinned validated numeric address. / 固定的已校验数字地址。
            path: Request path and query string. / 请求路径与查询字符串。
            limit: Maximum body bytes to read. / 最大读取响应体字节数。

        Returns:
            Tuple of status, lowercase headers and bounded body bytes. /
            状态码、小写头部与有界响应体字节的元组。
        """
        connection = PinnedHTTPSConnection(host, address, self.settings.command_timeout_seconds)

        def read():
            """
            Send the GET, read bounded headers/body and close the connection. /
            发送 GET、读取有界头部与响应体并关闭连接。

            Returns:
                Tuple of status, lowercase headers and bounded body bytes. /
                状态码、小写头部与有界响应体字节的元组。
            """
            try:
                if connection.cancelled.is_set():
                    raise OSError("Connection cancelled")
                connection.request(
                    "GET",
                    path,
                    headers={
                        "Accept": "text/html, text/plain, application/json",
                        "Accept-Encoding": "identity",
                        "User-Agent": "Slothy/1.0",
                        "Connection": "close",
                    },
                )
                response = connection.getresponse()
                headers = {name.lower(): value for name, value in response.getheaders()}
                if response.status in {301, 302, 303, 307, 308}:
                    return response.status, headers, b""
                return response.status, headers, response.read(limit + 1)
            finally:
                connection.close()

        try:
            return await asyncio.to_thread(read)
        except asyncio.CancelledError:
            connection.cancelled.set()
            if connection.sock is not None:
                try:
                    connection.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            connection.close()
            raise

    async def run(self, url: str, max_bytes=100_000):
        """
        Fetch text from an allowlisted HTTPS URL with redirect checks. /
        带重定向校验地从允许列表 HTTPS URL 获取文本。

        Args:
            url: Absolute HTTPS URL on the configured allowlist. / 配置允许列表中的绝对 HTTPS
            URL。
            max_bytes: Requested byte cap, further limited by settings. /
            请求字节上限，仍受配置进一步限制。

        Returns:
            Serialized ToolResult with final URL, bounded text and redirect chain. /
                含最终 URL、有界文本与重定向链的序列化 ToolResult。

        Raises:
            ToolOperationError: If retrieval is disabled, blocked, fails or redirects too
            often. /
                抓取被禁用、被阻断、失败或重定向过多时抛出。
        """
        if not self.settings.web_fetch_enabled:
            raise ToolOperationError("UNAVAILABLE", "HTTPS retrieval is disabled.")
        limit = min(max_bytes, self.settings.web_fetch_max_bytes)
        chain = []
        for _ in range(6):
            parsed, host = self.validate_url(url)
            addresses = await self.addresses(host)
            # English: Re-check even when a custom resolver is injected.
            # 中文：即使注入了自定义解析器也要重新检查。
            if not addresses or any(not public_address(item) for item in addresses):
                raise ToolOperationError(
                    "NETWORK_BLOCKED", "Only public network addresses are allowed."
                )
            path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            try:
                status, headers, body = await self.request(host, addresses[0], path, limit)
            except (OSError, http.client.HTTPException) as error:
                raise ToolOperationError(
                    "EXEC_FAILED", "HTTPS retrieval failed.", retryable=True
                ) from error
            chain.append(
                {
                    "url": urlunsplit(
                        (parsed.scheme, parsed.netloc, parsed.path, parsed.query, "")
                    ),
                    "status": status,
                }
            )
            if status in {301, 302, 303, 307, 308}:
                location = headers.get("location")
                if not location:
                    raise ToolOperationError("EXEC_FAILED", "Redirect has no location.")
                url = urljoin(url, location)
                continue
            if status < 200 or status >= 300:
                raise ToolOperationError("EXEC_FAILED", f"HTTPS server returned status {status}.")
            if headers.get("content-encoding", "identity").lower() != "identity":
                raise ToolOperationError("EXEC_FAILED", "Compressed responses are unsupported.")
            media = headers.get("content-type", "text/plain").split(";")[0].strip().lower()
            if media not in {
                "text/html",
                "text/plain",
                "application/json",
                "application/xhtml+xml",
            }:
                raise ToolOperationError("INVALID_ARGS", "The URL did not return supported text.")
            text = body[:limit].decode("utf-8", errors="replace")
            if media in {"text/html", "application/xhtml+xml"}:
                parser = TextExtractor()
                parser.feed(text)
                text = "".join(parser.parts).strip()
            cap = max(1, self.settings.max_output_chars - 3000)
            return ToolResult(
                ok=True,
                summary="Retrieved HTTPS reference text.",
                data={
                    "url": chain[-1]["url"],
                    "text": text[:cap],
                    "redirects": chain,
                    "truncated": len(body) > limit or len(text) > cap,
                },
            ).model_dump()
        raise ToolOperationError("NETWORK_BLOCKED", "Too many HTTPS redirects.")
