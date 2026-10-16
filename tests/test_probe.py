import socket
import time

from hq.probe import probe_all


def listening_socket():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    return server, server.getsockname()[1]


def closed_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_open_port_is_online():
    server, port = listening_socket()
    try:
        assert probe_all(["127.0.0.1"], port=port) == {"127.0.0.1": True}
    finally:
        server.close()


def test_closed_port_is_offline():
    assert probe_all(["127.0.0.1"], port=closed_port()) == {"127.0.0.1": False}


def test_unresolvable_host_is_offline():
    assert probe_all(["no-such-host.invalid"]) == {"no-such-host.invalid": False}


def test_empty_list_and_duplicates():
    assert probe_all([]) == {}
    assert list(probe_all(["127.0.0.1", "127.0.0.1"], port=closed_port())) == ["127.0.0.1"]


def test_fifty_hosts_are_checked_in_parallel():
    hosts = [f"127.0.0.{i}" for i in range(1, 51)]
    start = time.monotonic()
    result = probe_all(hosts, port=closed_port(), timeout=1.0)
    assert len(result) == 50 and not any(result.values())
    assert time.monotonic() - start < 3
