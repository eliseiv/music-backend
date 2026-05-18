from __future__ import annotations

import time

from app.middleware.rate_limit import TokenBucket


def test_token_bucket_consumes_until_empty():
    now = 1000.0
    bucket = TokenBucket(capacity=3, refill_per_sec=0.5, tokens=3, last_refill=now)
    for _ in range(3):
        ok, _ = bucket.consume(now)
        assert ok is True
    rejected, retry = bucket.consume(now)
    assert rejected is False
    assert retry > 0


def test_token_bucket_refills_over_time():
    now = 1000.0
    bucket = TokenBucket(capacity=2, refill_per_sec=1.0, tokens=0, last_refill=now)
    ok, retry = bucket.consume(now + 1.0)
    assert ok is True
    assert retry == 0.0


def test_token_bucket_caps_at_capacity():
    now = 1000.0
    bucket = TokenBucket(capacity=2, refill_per_sec=10.0, tokens=0, last_refill=now)
    bucket.consume(now + 100.0)
    assert bucket.tokens <= bucket.capacity
