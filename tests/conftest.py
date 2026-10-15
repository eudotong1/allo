import socket

import pytest


class NetworkBlocked(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Every test runs offline. Any attempt to open a connection fails loudly instead of reaching out."""
    def refuse(*args, **kwargs):
        raise NetworkBlocked("tests must not use the network")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


@pytest.fixture(autouse=True)
def isolated_config(tmp_path_factory, monkeypatch):
    """Point every test (and every subprocess it starts) at an empty config folder, so a computer that ran
    `allo-router connect` never uploads test jobs to the real dashboard."""
    monkeypatch.setenv("ALLO_ROUTER_CONFIG_DIR", str(tmp_path_factory.mktemp("allo-config")))
