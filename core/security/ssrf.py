"""SSRF 防护与安全 URL 抓取（审计 F-10：防护与 URL 摄取能力同批交付）。

防护层：
1. 协议白名单（仅 http/https）
2. 域名白名单（可选，按 KB 配置；空 = 任意公网域名）
3. 解析后 IP 校验：拒绝 private/loopback/link-local/reserved/multicast/unspecified，
   含 IPv4-mapped IPv6 形态；DNS 解析的全部地址逐一校验（防 DNS rebinding）
4. 重定向逐跳重校验（每跳重新解析+校验，最多 N 跳）
5. 连接超时 + 响应体大小上限（流式读取，防无限下载）
"""
import ipaddress
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

MAX_BODY_BYTES = 5 * 1024 * 1024  # 5MB
MAX_REDIRECTS = 3


class SsrfBlockedError(ValueError):
    """目标地址被 SSRF 防护拦截。"""


class FetchError(RuntimeError):
    """抓取失败（网络/超时/超限）。"""


@dataclass(frozen=True)
class FetchResult:
    title: str
    content: str
    final_url: str
    hops: int


def is_public_ip(ip_str: str) -> bool:
    """IP 必须为公网地址（IPv4/IPv6 全形态拒绝规则）。"""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def resolve_public(host: str) -> list[str]:
    """DNS 解析并校验全部地址为公网，返回排序后的公网 IP 列表。

    任一解析结果非公网即整体拒绝（防 rebinding 的第一道防线）；
    调用方必须固定连接返回的 IP（第二道防线：杜绝解析-连接间的 TOCTOU，审计 M-1）。
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise SsrfBlockedError(f"域名解析失败：{host}（{exc}）") from exc
    addrs = {str(info[4][0]) for info in infos}  # getaddrinfo 地址元组首元素收窄为 str
    publics: list[str] = []
    for addr in addrs:
        # IPv4-mapped IPv6（::ffff:x.x.x.x）解包回 IPv4 再判
        candidate = addr
        if candidate.startswith("::ffff:"):
            candidate = candidate.removeprefix("::ffff:")
        if not is_public_ip(candidate):
            raise SsrfBlockedError(f"目标解析到非公网地址：{addr}（主机 {host}）")
        publics.append(candidate)
    return sorted(publics)


def _host_allowed(host: str, allowlist: set[str]) -> bool:
    if not allowlist:
        return True
    if host in allowlist:
        return True
    for entry in allowlist:
        # 安全审计 L-2：条目必须是可注册域名（至少一段点）——公共后缀
        # （如 "com"）作为白名单条目会退化为任意域放行，启动校验兜底之外
        # 此处也拒绝对单段条目做后缀匹配
        if "." not in entry:
            continue
        if host.endswith(f".{entry}"):
            return True
    return False


class _HtmlTextExtractor(HTMLParser):
    """提取 HTML 正文文本与标题（去 script/style）。"""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self._skip += 1
        if tag in {"p", "br", "div", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip == 0 and data.strip():
            self.parts.append(data.strip())


def html_to_text(html: str) -> str:
    parser = _HtmlTextExtractor()
    parser.feed(html)
    return "\n".join(parser.parts)


def extract_title(html: str) -> str:
    import re

    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else ""


class UrlFetcher:
    """安全 URL 抓取（防护层 1-5 全链路）。"""

    def __init__(
        self,
        allowlist: set[str] | None = None,
        max_redirects: int = MAX_REDIRECTS,
        max_bytes: int = MAX_BODY_BYTES,
        timeout: float = 15.0,
        allow_loopback: bool = False,  # 仅测试用：本机假服务器
        transport: httpx.BaseTransport | None = None,  # 测试注入
    ) -> None:
        self._allowlist = allowlist or set()
        self._max_redirects = max_redirects
        self._max_bytes = max_bytes
        self._allow_loopback = allow_loopback
        self._client = httpx.Client(
            timeout=timeout, follow_redirects=False, trust_env=False, transport=transport
        )

    def _resolve_host(self, host: str) -> str | None:
        """域名 → 固定连接的公网 IP；IP 字面量校验后原样；测试模式返回 None（直连）。

        审计 M-1：解析校验与连接必须使用同一次解析结果——固定第一个已验证 IP，
        消除"校验后 DNS 变更指向内网"的 TOCTOU 窗口（Host/SNI 保持原域名）。
        """
        if self._allow_loopback:
            return None  # 测试/开发：本机假服务器，不做 IP 固定
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass  # 域名
        else:
            if not is_public_ip(host):
                raise SsrfBlockedError(f"目标地址非公网：{host}")
            return None  # 已是 IP 字面量，无需改写
        ips = resolve_public(host)
        return ips[0]

    def fetch(self, url: str) -> FetchResult:
        current = url
        for hops in range(self._max_redirects + 1):
            parsed = urlparse(current)
            if parsed.scheme not in {"http", "https"}:
                raise SsrfBlockedError(f"协议不允许：{parsed.scheme}（仅 http/https）")
            host = parsed.hostname or ""
            if not host:
                raise SsrfBlockedError("URL 缺少主机名")
            if not _host_allowed(host, self._allowlist):
                raise SsrfBlockedError(f"域名不在白名单：{host}")
            # 安全审计 L-1：非法端口（urlparse 解析异常）转防护错误而非 500
            try:
                parsed_port = parsed.port
            except ValueError as exc:
                raise SsrfBlockedError(f"端口非法：{current}") from exc
            # 逐跳解析+校验+固定 IP（防护层 3/4；M-1 修复）
            ip_target = self._resolve_host(host)
            if ip_target is not None:
                port = f":{parsed_port}" if parsed_port else ""
                connect_url = parsed._replace(netloc=f"{ip_target}{port}").geturl()
                host_header = host if not parsed_port else f"{host}:{parsed_port}"
                resp = self._client.get(
                    connect_url,
                    headers={"Host": host_header},
                    extensions={"sni_hostname": host},
                )
            else:
                resp = self._client.get(current)
            if resp.is_redirect:
                location = resp.headers.get("location")
                if not location:
                    raise FetchError("重定向缺少 Location 头")
                current = str(httpx.URL(current).join(location))
                if hops >= self._max_redirects:
                    raise FetchError(f"重定向超过上限（{self._max_redirects} 跳）")
                continue
            resp.raise_for_status()
            # 流式读取 + 大小上限（防护层 5）
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_bytes(1024 * 64):
                total += len(chunk)
                if total > self._max_bytes:
                    raise FetchError(f"响应体超过上限（{self._max_bytes // 1024 // 1024}MB）")
                chunks.append(chunk)
            html = b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")
            return FetchResult(
                title=extract_title(html) or urlparse(current).hostname or "网页",
                content=html_to_text(html),
                final_url=str(resp.url),
                hops=hops,
            )
        raise FetchError("重定向循环未收敛")
