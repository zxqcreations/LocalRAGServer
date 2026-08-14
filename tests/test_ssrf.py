"""SSRF 防护与 URL 抓取测试（审计 F-10；fake_site 假服务器在 conftest）。"""
import pytest

from core.security.ssrf import (
    FetchError,
    SsrfBlockedError,
    UrlFetcher,
    html_to_text,
    is_public_ip,
    resolve_public,
)

# ---------- IP 判定矩阵 ----------


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "127.8.8.8",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",  # 云元数据/链路本地
        "0.0.0.0",
        "::1",
        "fc00::1",  # ULA
        "fe80::1",  # link-local v6
        "::ffff:127.0.0.1",  # IPv4-mapped IPv6 回环
    ],
)
def test_blocked_ips(ip):
    assert not is_public_ip(ip), f"{ip} 应被判定为非公网"


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "114.114.114.114", "2606:4700::1111"])
def test_public_ips(ip):
    assert is_public_ip(ip)


def test_resolve_public_blocks_private(monkeypatch):
    # DNS 解析到私网地址 → 拒绝（防 rebinding）
    monkeypatch.setattr(
        "core.security.ssrf.socket.getaddrinfo", lambda host, port: [(0, 0, 0, "", ("10.0.0.5", 0))]
    )
    with pytest.raises(SsrfBlockedError):
        resolve_public("evil.example.com")


def test_resolve_public_allows_public(monkeypatch):
    monkeypatch.setattr(
        "core.security.ssrf.socket.getaddrinfo", lambda host, port: [(0, 0, 0, "", ("8.8.8.8", 0))]
    )
    resolve_public("ok.example.com")


# ---------- 协议与白名单 ----------


def test_fetcher_rejects_non_http_scheme():
    fetcher = UrlFetcher(allow_loopback=True)
    for bad in ("file:///etc/passwd", "ftp://example.com/x", "gopher://x"):
        with pytest.raises(SsrfBlockedError):
            fetcher.fetch(bad)


def test_fetcher_rejects_host_not_in_allowlist():
    fetcher = UrlFetcher(allowlist={"example.com"}, allow_loopback=True)
    with pytest.raises(SsrfBlockedError):
        fetcher.fetch("http://other.com/x")


def test_fetcher_allowlist_logic():
    # 白名单匹配为纯逻辑（网络层由 respx 护栏与外联测试覆盖）
    from core.security.ssrf import _host_allowed

    assert _host_allowed("sub.example.com", {"example.com"})  # 子域名放行
    assert not _host_allowed("other.com", {"example.com"})
    assert not _host_allowed("notexample.com", {"example.com"})  # 后缀陷阱
    assert _host_allowed("anything.com", set())  # 空白名单 = 放行（IP 校验兜底）


# ---------- 抓取集成（conftest 假服务器 + allow_loopback 测试开关） ----------


def test_fetch_extracts_title_and_text(fake_site):
    fetcher = UrlFetcher(allow_loopback=True)
    result = fetcher.fetch(fake_site.url)
    assert result.title == "测试"
    assert "正文内容。" in result.content
    assert "bad()" not in result.content  # script 被剥离


def test_fetch_blocked_without_loopback_flag(fake_site):
    fetcher = UrlFetcher()  # 默认拒绝回环
    with pytest.raises(SsrfBlockedError):
        fetcher.fetch(fake_site.url)


def test_redirect_hop_limit(fake_site):
    # 服务器连续 302 到自身 → 超过 3 跳上限
    fake_site.RequestHandlerClass.location = fake_site.url
    fetcher = UrlFetcher(allow_loopback=True, max_redirects=3)
    with pytest.raises(FetchError, match="重定向"):
        fetcher.fetch(fake_site.url)


def test_body_size_limit(fake_site):
    fake_site.RequestHandlerClass.body = b"x" * (2 * 1024 * 1024)
    fetcher = UrlFetcher(allow_loopback=True, max_bytes=1024 * 1024)
    with pytest.raises(FetchError, match="上限"):
        fetcher.fetch(fake_site.url)


def test_rebinding_fix_connects_to_resolved_ip(monkeypatch):
    # 审计 M-1 核心断言：域名解析后必须固定连接已验证 IP，Host/SNI 保持原域名
    import httpx

    monkeypatch.setattr(
        "core.security.ssrf.socket.getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("8.8.8.8", 0))],
    )
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["host"] = request.headers.get("Host", "")
        return httpx.Response(200, text="<html><head><title>ok</title></head></html>")

    fetcher = UrlFetcher(transport=httpx.MockTransport(handler))
    result = fetcher.fetch("http://example.com/page")
    assert seen["url"] == "http://8.8.8.8/page"  # 连接目标 = 已验证 IP
    assert seen["host"] == "example.com"  # Host 头保持原域名
    assert result.title == "ok"


def test_rebinding_fix_redirects_revalidate_each_hop(monkeypatch):
    # 每跳重定向都重新解析+固定（首跳 8.8.8.8，重定向到 second.com → 1.1.1.1）
    import httpx

    resolves = {"example.com": "8.8.8.8", "second.com": "1.1.1.1"}

    def fake_getaddrinfo(host, port):
        return [(2, 1, 6, "", (resolves[host], 0))]

    monkeypatch.setattr("core.security.ssrf.socket.getaddrinfo", fake_getaddrinfo)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "8.8.8.8":
            return httpx.Response(302, headers={"Location": "http://second.com/final"})
        return httpx.Response(200, text="<html><head><title>ok</title></head></html>")

    fetcher = UrlFetcher(transport=httpx.MockTransport(handler))
    result = fetcher.fetch("http://example.com/start")
    assert seen == ["http://8.8.8.8/start", "http://1.1.1.1/final"]  # 每跳固定解析 IP
    assert result.title == "ok"


def test_html_to_text_basic():
    assert "你好" in html_to_text("<p>你好</p><script>ignore()</script>")
