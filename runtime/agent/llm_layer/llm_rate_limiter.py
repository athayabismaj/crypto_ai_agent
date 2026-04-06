"""
Token bucket algorithm untuk API LLM.
Tidak memblokir main loop (non-blocking) — kembalikan False jika limit tercapai.
"""

import time


class TokenBucket:
    def __init__(self, limit: int, replenish_interval_s: float = 60.0):
        self.capacity = limit
        self.tokens = limit
        self.interval = replenish_interval_s
        self.last_update = time.time()

    def consume(self, amount: int) -> bool:
        now = time.time()
        # Replenish
        passed = now - self.last_update
        if passed > 0:
            add_tokens = (passed / self.interval) * self.capacity
            self.tokens = min(self.capacity, self.tokens + add_tokens)
            self.last_update = now
            
        if self.tokens >= amount:
            self.tokens -= amount
            return True
        return False


class LLMRateLimiter:
    """Token bucket algorithm. Tidak memblokir --- return False jika limit tercapai."""
    def __init__(
        self,
        calls_per_minute: int = 50,  # Limits for Tier 1 Anthropic API
        tokens_per_minute: int = 40_000,
    ):
        self._call_bucket = TokenBucket(calls_per_minute, 60.0)
        self._token_bucket = TokenBucket(tokens_per_minute, 60.0)

    def acquire(self, estimated_tokens: int = 500) -> bool:
        """Non-blocking. Return False jika rate limit tercapai."""
        # Check both condition separately so we don't accidentally consume calls if token empty
        now = time.time()
        
        # update both buckets
        passed = now - self._call_bucket.last_update
        add_calls = (passed / 60.0) * self._call_bucket.capacity
        self._call_bucket.tokens = min(self._call_bucket.capacity, self._call_bucket.tokens + add_calls)
        self._call_bucket.last_update = now
        
        passed_t = now - self._token_bucket.last_update
        add_toks = (passed_t / 60.0) * self._token_bucket.capacity
        self._token_bucket.tokens = min(self._token_bucket.capacity, self._token_bucket.tokens + add_toks)
        self._token_bucket.last_update = now
        
        if self._call_bucket.tokens >= 1 and self._token_bucket.tokens >= estimated_tokens:
            self._call_bucket.tokens -= 1
            self._token_bucket.tokens -= estimated_tokens
            return True
        return False
