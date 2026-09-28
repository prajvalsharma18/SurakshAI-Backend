"""Authentication-only security boundary for the WorkWell prototype."""

from .auth import authenticate_request, authenticate_user, require_auth
from .audit import AuditService, RESULT_DENIED, RESULT_FAILURE, RESULT_SUCCESS, get_audit_service
from .consent import CONSENT_TYPES, ConsentService
from .data_minimization import DataMinimizer
from .pseudonymization import PseudonymizationError, Pseudonymizer
from .rbac import authorize, has_permission, require_employee_access, require_permission
from .token import TokenError, TokenManager

__all__ = [
    'TokenError',
    'TokenManager',
    'AuditService',
    'get_audit_service',
    'CONSENT_TYPES',
    'ConsentService',
    'DataMinimizer',
    'PseudonymizationError',
    'Pseudonymizer',
    'RESULT_DENIED',
    'RESULT_FAILURE',
    'RESULT_SUCCESS',
    'authenticate_request',
    'authenticate_user',
    'authorize',
    'has_permission',
    'require_employee_access',
    'require_auth',
    'require_permission',
]
