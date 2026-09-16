"""Trusted-proxy-aware office-network verification for attendance.

The browser never supplies an authoritative address. We use the TCP peer and
only consult X-Forwarded-For when that peer is in the explicitly configured
trusted-proxy set. Traversing the chain from right to left prevents a client
from winning by prepending a forged address to the header.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
from dataclasses import dataclass
from typing import Iterable


OFFICE_NETWORK_ENV = "OPERATIONS_OFFICE_NETWORK_CIDRS"
TRUSTED_PROXY_ENV = "OPERATIONS_TRUSTED_PROXY_CIDRS"


@dataclass(frozen=True)
class NetworkVerification:
    allowed: bool
    source: str
    policy_id: str
    reason: str
    #: The address the decision was made about. A refused handler is told it so
    #: the office allowlist can be corrected without reading a server log: the
    #: office public IP changes whenever the router is reset -- it did in
    #: September 2026 -- and until the refusal named an address, the only
    #: symptom was "Connect to Office Wi-Fi" while sitting in the office. The
    #: allowlist itself is configuration (OPERATIONS_OFFICE_NETWORK_CIDRS), so
    #: no office address is written down here.
    observed_ip: str = ""

    def audit_payload(self) -> dict[str, str | bool]:
        return {
            "verified": self.allowed,
            "source": self.source,
            "policy_id": self.policy_id,
            "reason": self.reason,
            "observed_ip": self.observed_ip,
        }


def _networks(raw: str) -> tuple[ipaddress._BaseNetwork, ...]:
    values: list[ipaddress._BaseNetwork] = []
    for item in str(raw or "").split(","):
        value = item.strip()
        if not value:
            continue
        try:
            values.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            # Invalid configuration fails closed; it never broadens access.
            continue
    return tuple(values)


def _address(value: object) -> ipaddress._BaseAddress | None:
    try:
        return ipaddress.ip_address(str(value or "").strip())
    except ValueError:
        return None


def _inside(address: ipaddress._BaseAddress | None, networks: Iterable[ipaddress._BaseNetwork]) -> bool:
    return bool(address and any(address.version == network.version and address in network for network in networks))


def _policy_id(raw: str) -> str:
    normalized = ",".join(sorted(item.strip() for item in str(raw or "").split(",") if item.strip()))
    if not normalized:
        return "unconfigured"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def authoritative_client_ip(
    peer_host: object,
    forwarded_for: object,
    *,
    trusted_proxy_cidrs: str | None = None,
) -> tuple[ipaddress._BaseAddress | None, str]:
    """Resolve the client address without trusting client-controlled headers."""

    peer = _address(peer_host)
    trusted = _networks(
        os.environ.get(TRUSTED_PROXY_ENV, "")
        if trusted_proxy_cidrs is None
        else trusted_proxy_cidrs
    )
    if not peer or not _inside(peer, trusted):
        return peer, "direct"

    chain = [_address(part) for part in str(forwarded_for or "").split(",")]
    valid_chain = [address for address in chain if address is not None]
    for address in reversed(valid_chain):
        if not _inside(address, trusted):
            return address, "trusted_proxy"
    return (valid_chain[0], "trusted_proxy") if valid_chain else (peer, "direct")


def verify_office_network(
    peer_host: object,
    forwarded_for: object = "",
    *,
    office_cidrs: str | None = None,
    trusted_proxy_cidrs: str | None = None,
) -> NetworkVerification:
    raw_office = (
        os.environ.get(OFFICE_NETWORK_ENV, "")
        if office_cidrs is None
        else office_cidrs
    )
    office = _networks(raw_office)
    policy = _policy_id(raw_office)
    if not office:
        return NetworkVerification(False, "unverified", policy, "OFFICE_NETWORK_NOT_CONFIGURED")
    client, source = authoritative_client_ip(
        peer_host,
        forwarded_for,
        trusted_proxy_cidrs=trusted_proxy_cidrs,
    )
    if not client:
        return NetworkVerification(False, source, policy, "CLIENT_ADDRESS_UNAVAILABLE")
    if _inside(client, office):
        return NetworkVerification(True, source, policy, "APPROVED_OFFICE_NETWORK", str(client))
    return NetworkVerification(False, source, policy, "OUTSIDE_APPROVED_OFFICE_NETWORK", str(client))
