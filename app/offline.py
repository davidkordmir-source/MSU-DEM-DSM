"""A local-only policy for the portable desktop launcher, without modifying OS settings."""

import ipaddress
import os
import socket
import sys


def is_loopback(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="replace")
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(str(host).split("%", 1)[0])
        return address.is_loopback or bool(
            isinstance(address, ipaddress.IPv6Address)
            and address.ipv4_mapped
            and address.ipv4_mapped.is_loopback
        )
    except ValueError:
        return False


def network_guard(event, args):
    if event == "socket.getaddrinfo":
        host = args[0]
        if host is not None and not is_loopback(host):
            raise PermissionError("MSU Terrain offline mode: external DNS is disabled")
    elif event in ("socket.connect", "socket.sendto"):
        sock, address = args[0], args[-1]
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not is_loopback(address[0]):
            raise PermissionError("MSU Terrain offline mode: external connections are disabled")


def enable_offline_mode():
    # PROJ's native HTTP client is not covered by Python socket audit hooks.
    os.environ["PROJ_NETWORK"] = "OFF"
    sys.addaudithook(network_guard)
