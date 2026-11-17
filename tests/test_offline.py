import socket
from types import SimpleNamespace

import pytest

from app.offline import is_loopback, network_guard


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"])
def test_local_connections_allowed(host):
    assert is_loopback(host)
    network_guard("socket.getaddrinfo", (host, 8000, 0, 0, 0))
    network_guard("socket.connect", (SimpleNamespace(family=socket.AF_INET), (host, 8000)))


@pytest.mark.parametrize("host", ["example.com", "192.168.1.1", "1.1.1.1", "0.0.0.0"])
def test_nonlocal_connections_blocked(host):
    assert not is_loopback(host)
    with pytest.raises(PermissionError):
        network_guard("socket.getaddrinfo", (host, 443, 0, 0, 0))
    with pytest.raises(PermissionError):
        network_guard("socket.connect", (SimpleNamespace(family=socket.AF_INET), (host, 443)))
