"""Security policy helpers for crawler targets."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def domain_matches(hostname: str, patterns: set[str]) -> bool:
    host = hostname.lower().strip(".")
    for pattern in patterns:
        item = pattern.lower().lstrip(".").rstrip(".")
        if host == item or host.endswith("." + item):
            return True
    return False


def host_addresses(hostname: str) -> list[ipaddress._BaseAddress]:
    host = hostname.strip("[]")
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"Target hostname cannot be resolved: {hostname}") from exc

    addresses = []
    seen = set()
    for info in infos:
        address = info[4][0]
        if address in seen:
            continue
        seen.add(address)
        addresses.append(ipaddress.ip_address(address))
    return addresses


def is_private_target(address: ipaddress._BaseAddress) -> bool:
    return any([
        address.is_private,
        address.is_loopback,
        address.is_link_local,
        address.is_multicast,
        address.is_reserved,
        address.is_unspecified,
    ])


def effective_allow_private(allow_private: bool, *, request_override_enabled: bool) -> bool:
    if allow_private and not request_override_enabled:
        raise PermissionError(
            "Request-level allow_private=True is disabled; enable "
            "CRAWLER_ALLOW_REQUEST_PRIVATE_OVERRIDE=true or CRAWLER_ALLOW_PRIVATE_NETS=true "
            "only in a trusted environment."
        )
    return allow_private


def effective_verify_tls(verify_tls: bool, *, insecure_override_enabled: bool) -> bool:
    if not verify_tls and not insecure_override_enabled:
        raise PermissionError(
            "Request-level verify_tls=False is disabled; enable "
            "CRAWLER_ALLOW_INSECURE_TLS_OVERRIDE=true only in a trusted environment."
        )
    return verify_tls


def validate_url(
    url: str,
    *,
    allow_private: bool = False,
    allow_private_nets: bool = False,
    allowed_domains: set[str] | None = None,
    blocked_domains: set[str] | None = None,
) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise ValueError("Only http/https URLs are supported")

    host = parsed.hostname.lower()
    allowed_domains = allowed_domains or set()
    blocked_domains = blocked_domains or set()

    if blocked_domains and domain_matches(host, blocked_domains):
        raise ValueError(f"Target domain is blocked: {host}")
    if allowed_domains and not domain_matches(host, allowed_domains):
        raise ValueError(f"Target domain is not in the allowlist: {host}")

    if not (allow_private or allow_private_nets):
        blocked_addresses = [addr for addr in host_addresses(host) if is_private_target(addr)]
        if blocked_addresses:
            addr_text = ", ".join(str(addr) for addr in blocked_addresses[:3])
            raise ValueError(
                f"Private/local/reserved targets are blocked by default: {host} -> {addr_text}; "
                "pass allow_private=True only when explicitly trusted."
            )
    return url
