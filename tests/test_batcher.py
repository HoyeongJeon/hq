from hq.batcher import OutputBatcher


def test_drain_keeps_order_per_session_and_empties():
    batcher = OutputBatcher()
    batcher.add("a", "1")
    batcher.add("b", "x")
    batcher.add("a", "2")
    assert batcher.drain() == {"a": "12", "b": "x"}
    assert batcher.drain() == {}
    assert batcher.pending() == 0


def test_drain_is_capped_and_the_rest_waits_for_the_next_round():
    batcher = OutputBatcher(max_chars=5)
    batcher.add("a", "1234567")
    assert batcher.drain() == {"a": "12345"}
    assert batcher.pending() == 2
    assert batcher.drain() == {"a": "67"}


def test_cap_spans_sessions_without_losing_anything():
    batcher = OutputBatcher(max_chars=4)
    batcher.add("a", "aaa")
    batcher.add("b", "bbb")
    first = batcher.drain()
    second = batcher.drain()
    assert first == {"a": "aaa", "b": "b"}
    assert second == {"b": "bb"}


def test_discard_drops_a_closed_tab():
    batcher = OutputBatcher()
    batcher.add("a", "123")
    batcher.add("b", "x")
    batcher.discard("a")
    assert batcher.drain() == {"b": "x"}
    assert batcher.pending() == 0


def test_empty_data_is_ignored():
    batcher = OutputBatcher()
    batcher.add("a", "")
    assert batcher.drain() == {}
