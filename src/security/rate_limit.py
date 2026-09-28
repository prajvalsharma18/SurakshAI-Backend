"""Small process-local rate limiter for sensitive API entry points."""

from collections import deque
from functools import wraps
from threading import Lock
from time import monotonic

from flask import jsonify, make_response, request


_attempts = {}
_lock = Lock()
_MAX_CLIENT_BUCKETS = 10000


def rate_limit(*, limit, window_seconds, bucket, count_status_codes=None):
    """Limit calls per client address and route bucket within one process."""
    if limit < 1 or window_seconds < 1:
        raise ValueError('Rate limit and window must be positive')

    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            now = monotonic()
            key = (bucket, request.remote_addr or 'unknown')
            with _lock:
                if key not in _attempts and len(_attempts) >= _MAX_CLIENT_BUCKETS:
                    expired = [
                        existing_key for existing_key, events in _attempts.items()
                        if not events or now - events[-1] >= window_seconds
                    ]
                    for existing_key in expired:
                        _attempts.pop(existing_key, None)
                    if len(_attempts) >= _MAX_CLIENT_BUCKETS:
                        response = jsonify({'error': 'Too many requests'})
                        response.status_code = 429
                        response.headers['Retry-After'] = str(window_seconds)
                        return response
                attempts = _attempts.setdefault(key, deque())
                while attempts and now - attempts[0] >= window_seconds:
                    attempts.popleft()
                if len(attempts) >= limit:
                    retry_after = max(1, int(window_seconds - (now - attempts[0])))
                    response = jsonify({'error': 'Too many requests'})
                    response.status_code = 429
                    response.headers['Retry-After'] = str(retry_after)
                    return response
                if count_status_codes is None:
                    attempts.append(now)
            response = make_response(function(*args, **kwargs)) if count_status_codes is not None else function(*args, **kwargs)
            if count_status_codes is not None and response.status_code in count_status_codes:
                with _lock:
                    _attempts.setdefault(key, deque()).append(now)
            return response
        return wrapped
    return decorate


def reset_rate_limits():
    """Clear process-local counters for deterministic tests and shutdown hooks."""
    with _lock:
        _attempts.clear()
