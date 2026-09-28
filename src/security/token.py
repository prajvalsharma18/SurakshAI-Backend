"""JWT creation and validation for Phase 2A authentication."""

from datetime import datetime, timedelta, timezone
import os

import jwt

from config import (
    get_jwt_access_token_expires_minutes,
    get_jwt_secret_key,
)


class TokenError(ValueError):
    """Raised when a JWT is absent, malformed, expired, or invalid."""


class TokenManager:
    def __init__(self, secret_key=None, algorithm=None, expires_minutes=None):
        self.secret_key = secret_key or get_jwt_secret_key()
        self.algorithm = algorithm or os.getenv('JWT_ALGORITHM', 'HS256')
        self.expires_minutes = (
            get_jwt_access_token_expires_minutes() if expires_minutes is None else int(expires_minutes)
        )
        if not self.secret_key:
            raise ValueError('JWT_SECRET_KEY must be configured')
        if self.algorithm not in {'HS256', 'HS384', 'HS512'}:
            raise ValueError('JWT_ALGORITHM must be HS256, HS384, or HS512')
        if not 1 <= self.expires_minutes <= 60:
            raise ValueError('JWT access token lifetime must be between 1 and 60 minutes')
        self.issuer = os.getenv('JWT_ISSUER', 'surakshai-api')
        self.audience = os.getenv('JWT_AUDIENCE', 'surakshai-client')

    def create_access_token(self, user):
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=self.expires_minutes)
        payload = {
            'sub': user['user_id'],
            'username': user['username'],
            'role': user['role'],
            'type': 'access',
            'iss': self.issuer,
            'aud': self.audience,
            'iat': now,
            'exp': expires_at,
        }
        if user.get('personnel_id'):
            payload['personnel_id'] = user['personnel_id']
        token = jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
        return token, expires_at

    def decode_access_token(self, token):
        if not token:
            raise TokenError('Authorization token is required')
        try:
            claims = jwt.decode(
                token,
                self.secret_key,
                algorithms=[self.algorithm],
                issuer=self.issuer,
                audience=self.audience,
                options={'require': ['sub', 'username', 'role', 'type', 'iat', 'exp']}
            )
        except jwt.ExpiredSignatureError as error:
            raise TokenError('Token has expired') from error
        except jwt.InvalidTokenError as error:
            raise TokenError('Invalid token') from error
        if claims.get('type') != 'access':
            raise TokenError('Invalid token type')
        identity = {
            'user_id': claims['sub'],
            'username': claims['username'],
            'role': claims['role'],
        }
        if claims.get('personnel_id'):
            identity['personnel_id'] = claims['personnel_id']
        return identity
