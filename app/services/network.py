"""LAN/Hotspot IP filtering and active internet connectivity checks."""

from __future__ import annotations

import socket
from typing import Mapping

import psutil

INTERNET_PROBES: tuple[tuple[str, int], ...] = (
    ("1.1.1.1", 443),
    ("8.8.8.8", 53),
    ("smtp.gmail.com", 465),
)

VIRTUAL_IFACE_TOKENS: tuple[str, ...] = (
    "vethernet",
    "wsl",
    "hyper-v",
    "default switch",
    "vmware",
    "virtualbox",
    "vbox",
    "loopback",
    "teredo",
    "isatap",
    "npcap",
)


def is_virtual_interface(name: str) -> bool:
    """Return True if an interface name belongs to a local virtual switch/tunnel."""
    lower = name.lower()
    return any(token in lower for token in VIRTUAL_IFACE_TOKENS)


def filter_physical_lan_ips(
    if_addrs: Mapping[str, list],
    if_stats: Mapping[str, object] | None = None,
) -> list[str]:
    """Extract sorted real Wi-Fi/Ethernet/Hotspot IPv4 addresses, skipping virtual adapters."""
    ips: set[str] = set()
    for iface, addrs in if_addrs.items():
        if is_virtual_interface(iface):
            continue
        if if_stats is not None and iface in if_stats and not getattr(if_stats[iface], "isup", True):
            continue
        for addr in addrs:
            if getattr(addr, "family", None) != socket.AF_INET:
                continue
            ip = getattr(addr, "address", "")
            if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
                ips.add(ip)

    return sorted(ips, key=lambda ip: (0 if ip == "192.168.137.1" else 1, ip))


def get_lan_ips() -> list[str]:
    """Return sorted list of reachable local Wi-Fi, Ethernet, and Hotspot IPv4 addresses."""
    try:
        ips = filter_physical_lan_ips(psutil.net_if_addrs(), psutil.net_if_stats())
        if ips:
            return ips
    except Exception:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            primary = sock.getsockname()[0]
            if primary and not primary.startswith("127."):
                return [primary]
    except OSError:
        pass

    return []


def get_lan_urls(port: int) -> list[str]:
    """Build clickable HTTP URLs for reachable local Wi-Fi / Hotspot addresses."""
    return [f"http://{ip}:{port}" for ip in get_lan_ips()]


def has_active_internet(timeout: float = 2.5) -> bool:
    """Return True if outbound TCP internet connectivity is currently reachable."""
    for host, port in INTERNET_PROBES:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False
