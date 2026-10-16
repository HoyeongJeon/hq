import socket
import threading
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


def test_slow_name_lookups_do_not_hold_back_the_others(monkeypatch):
    real_getaddrinfo = socket.getaddrinfo
    release = threading.Event()

    def getaddrinfo(host, *args, **kwargs):
        if host.startswith("slow-"):
            release.wait(5)
        elif host == "fast-name":
            host = "127.0.0.1"
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    server, port = listening_socket()
    slow = [f"slow-{i}.invalid" for i in range(40)]
    timeout = 1.0
    try:
        start = time.monotonic()
        result = probe_all([*slow, "fast-name", "127.0.0.1"], port=port, timeout=timeout)
        elapsed = time.monotonic() - start
    finally:
        release.set()
        server.close()
    assert elapsed < timeout + 0.5
    assert result == {**dict.fromkeys(slow, False), "fast-name": True, "127.0.0.1": True}


def test_name_whose_first_address_does_not_answer_tries_the_next():
    # Windows의 localhost는 ::1을 먼저 준다. 127.0.0.1에서만 받으면 ::1 시도는 1초 안에 끝나지 않는다.
    server, port = listening_socket()
    try:
        assert probe_all(["localhost"], port=port) == {"localhost": True}
    finally:
        server.close()


def test_ip_only_list_starts_no_threads(monkeypatch):
    started = []
    real_start = threading.Thread.start

    def start(self):
        started.append(self.name)
        real_start(self)

    monkeypatch.setattr(threading.Thread, "start", start)
    probe_all(["127.0.0.1", "127.0.0.2"], port=closed_port(), timeout=0.2)
    assert started == []
