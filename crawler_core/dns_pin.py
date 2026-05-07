"""DNS rebinding 防护工具。

策略：第一次解析得到的 IP 锁住，后续 requests/curl_cffi 用这个 IP 直连，
但 SNI / Host header 仍用原 hostname，避免 https 证书校验失败。

requests：自定义 HTTPAdapter，在 socket.create_connection 之前替换 host→IP，
但保留 ServerHostname。
curl_cffi：用原生 resolve=["host:port:ip"] 参数。
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Iterable
from urllib.parse import urlparse


class PinnedAddress:
    """一次请求的 host→IP 绑定。"""

    __slots__ = ("hostname", "ip", "port", "scheme")

    def __init__(self, hostname: str, ip: str, port: int, scheme: str):
        self.hostname = hostname
        self.ip = ip
        self.port = port
        self.scheme = scheme

    def curl_resolve_entry(self) -> str:
        """生成 curl_cffi/curl 兼容的 resolve 项，例如 'example.com:443:1.2.3.4'。"""
        return f"{self.hostname}:{self.port}:{self.ip}"

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"PinnedAddress({self.hostname}->{self.ip}:{self.port})"


def parse_url_target(url: str) -> tuple[str, str, int]:
    """从 URL 取出 (scheme, hostname, port)。"""
    parsed = urlparse(url)
    scheme = (parsed.scheme or "https").lower()
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError(f"URL 缺少 host: {url}")
    port = parsed.port or (443 if scheme == "https" else 80)
    return scheme, hostname, port


def resolve_addresses(hostname: str) -> list[ipaddress._BaseAddress]:
    """用 socket.getaddrinfo 真实解析；hostname 本身就是 IP 时直接返回。"""
    host = hostname.strip("[]")
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"目标域名无法解析: {hostname}") from exc
    seen: set[str] = set()
    result: list[ipaddress._BaseAddress] = []
    for info in infos:
        addr = info[4][0]
        if addr in seen:
            continue
        seen.add(addr)
        result.append(ipaddress.ip_address(addr))
    return result


def pick_pinned_ip(addresses: Iterable[ipaddress._BaseAddress]) -> str:
    """从候选地址中挑一个用作 pinning。优先 IPv4，其次 IPv6。"""
    addresses = list(addresses)
    if not addresses:
        raise ValueError("没有可用的解析地址")
    for addr in addresses:
        if isinstance(addr, ipaddress.IPv4Address):
            return str(addr)
    return str(addresses[0])


def build_pinned_address(url: str, public_addresses: list[ipaddress._BaseAddress]) -> PinnedAddress:
    scheme, host, port = parse_url_target(url)
    ip = pick_pinned_ip(public_addresses)
    return PinnedAddress(host, ip, port, scheme)


# ---------- requests 适配 ----------

def make_pinned_requests_session(session, pinned: PinnedAddress):
    """在 session 上挂载一个 PinnedHTTPAdapter，仅作用于 pinned 的 host:port。

    注意：requests / urllib3 没有官方"per-request DNS"接口，我们走的是
    "把 URL 里的 host 替换成 IP 但保留 Host header 与 SNI"。
    返回一个修改过的 url，调用方应把 session.get(url=...) 用这个新 url 发。
    """
    import urllib3
    from requests.adapters import HTTPAdapter

    target_origin = f"{pinned.scheme}://{pinned.hostname}:{pinned.port}"

    class PinnedAdapter(HTTPAdapter):
        def get_connection(self, url, proxies=None):  # urllib3<2 路径
            return super().get_connection(url, proxies)

        def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):  # urllib3>=2
            return super().get_connection_with_tls_context(request, verify, proxies, cert)

        def init_poolmanager(self, *args, **kwargs):
            kwargs["server_hostname"] = pinned.hostname
            kwargs["assert_hostname"] = pinned.hostname
            super().init_poolmanager(*args, **kwargs)

        def send(self, request, **kwargs):
            # 在发送前确保 Host header 始终是原 hostname（防止被 IP 替换覆盖）
            request.headers.setdefault("Host", pinned.hostname)
            return super().send(request, **kwargs)

    adapter = PinnedAdapter()
    session.mount(target_origin + "/", adapter)
    return adapter


def rewrite_url_to_ip(url: str, pinned: PinnedAddress) -> str:
    """把 url 中的 hostname 改成 ip（保留 path/query/fragment），
    用于 requests.get(rewritten_url, headers={"Host": pinned.hostname})。
    """
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    # 处理 IPv6 字面量
    ip_part = pinned.ip if ":" not in pinned.ip else f"[{pinned.ip}]"
    new_netloc = f"{ip_part}:{pinned.port}" if pinned.port else ip_part
    if parts.username or parts.password:
        userinfo = parts.username or ""
        if parts.password:
            userinfo += f":{parts.password}"
        new_netloc = f"{userinfo}@{new_netloc}"
    return urlunsplit((parts.scheme, new_netloc, parts.path or "/", parts.query, parts.fragment))


def verify_ip_unchanged(hostname: str, original_addresses: list[ipaddress._BaseAddress]) -> bool:
    """二次解析比对：hostname 现在的解析结果是否与 original_addresses 相交。

    用于 browser 模式（无法 pin），只能在请求前再解析一次比对，
    若集合无交集说明发生了 DNS rebinding，应拒绝请求。
    """
    if not original_addresses:
        return False
    try:
        current = {str(addr) for addr in resolve_addresses(hostname)}
    except ValueError:
        return False
    original = {str(addr) for addr in original_addresses}
    return bool(current & original)
