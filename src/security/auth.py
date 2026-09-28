"""Authentication and Flask request authentication helpers."""

from functools import wraps

from flask import g, jsonify, request

from .audit import RESULT_FAILURE, get_audit_service
from .token import TokenError, TokenManager


def authenticate_user(username, password, user_service):
    """Authenticate only through the configured persistent user service."""
    return user_service.authenticate(username, password)


def authenticate_request(token_manager=None):
    authorization = request.headers.get('Authorization', '')
    scheme, _, token = authorization.partition(' ')
    if scheme.lower() != 'bearer' or not token:
        raise TokenError('Bearer token is required')
    manager = token_manager or TokenManager()
    identity = manager.decode_access_token(token.strip())
    g.authenticated_identity = identity
    return identity


def require_auth(function):
    @wraps(function)
    def decorated(*args, **kwargs):
        try:
            authenticate_request()
        except (TokenError, ValueError) as error:
            get_audit_service().record_event(
                action='AUTHENTICATION_FAILURE',
                result=RESULT_FAILURE,
                source=request.path,
                purpose='JWT validation',
            )
            return jsonify({'error': str(error)}), 401
        return function(*args, **kwargs)
    return decorated
