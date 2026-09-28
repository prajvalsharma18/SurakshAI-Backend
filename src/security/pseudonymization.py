"""Deterministic HMAC pseudonymization for internal processing."""

import hashlib
import hmac
import os


class PseudonymizationError(ValueError):
    """Raised when pseudonymization cannot be safely configured or applied."""


class Pseudonymizer:
    def __init__(self, secret=None):
        self.secret = secret or os.getenv('PSEUDONYMIZATION_SECRET')
        if not self.secret:
            raise PseudonymizationError('PSEUDONYMIZATION_SECRET must be configured')

    def pseudonymize(self, identifier):
        if identifier is None or str(identifier) == '':
            raise PseudonymizationError('An identifier is required for pseudonymization')
        digest = hmac.new(
            self.secret.encode('utf-8'),
            str(identifier).encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
        return f'psn_{digest}'
