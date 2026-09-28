"""Centralized role and resource authorization for the current API."""

from functools import wraps

from flask import g, jsonify, request

from .audit import RESULT_DENIED, get_audit_service
from .permissions import (
    PERMISSION_REGISTER_EMPLOYEE,
    PERMISSION_RUN_PREDICTION,
    PERMISSION_VIEW_EMPLOYEE,
    PERMISSION_VIEW_OWN_PROFILE,
    PERMISSION_VIEW_OWN_OPERATIONAL_RECORDS,
    PERMISSION_VIEW_OPERATIONAL_RECORDS,
    PERMISSION_VIEW_WELLNESS_RECORDS,
    ROLE_COMMANDER,
    ROLE_ADMIN,
    ROLE_PERMISSIONS,
    ROLE_PERSONNEL,
    ROLE_WELFARE_OFFICER,
)


class AuthorizationError(ValueError):
    """Raised when an authenticated identity lacks an allowed permission."""


def permissions_for(identity):
    return ROLE_PERMISSIONS.get(identity.get('role'), set())


def has_permission(identity, permission):
    return permission in permissions_for(identity)


def _requested_employee_id(resource_parameter):
    if resource_parameter in request.view_args:
        return request.view_args[resource_parameter]
    body = request.get_json(silent=True) or {}
    return body.get(resource_parameter) or body.get('employee_id')


def authorize(identity, permission, resource_parameter=None):
    if identity is None:
        raise AuthorizationError('Authentication required')
    role_permissions = permissions_for(identity)
    personnel_owns_resource = (
        permission == PERMISSION_VIEW_EMPLOYEE
        and identity.get('role') == ROLE_PERSONNEL
        and PERMISSION_VIEW_OWN_PROFILE in role_permissions
    )
    if not has_permission(identity, permission) and not personnel_owns_resource:
        raise AuthorizationError('Forbidden')

    if resource_parameter and identity.get('role') == ROLE_PERSONNEL:
        requested_id = _requested_employee_id(resource_parameter)
        if requested_id != identity.get('user_id'):
            raise AuthorizationError('Forbidden')

    return identity


def require_permission(permission, resource_parameter=None):
    def decorator(function):
        @wraps(function)
        def decorated(*args, **kwargs):
            try:
                authorize(g.get('authenticated_identity'), permission, resource_parameter)
            except AuthorizationError as error:
                identity = g.get('authenticated_identity') or {}
                view_args = request.view_args or {}
                get_audit_service().record_event(
                    actor_user_id=identity.get('user_id'),
                    actor_role=identity.get('role'),
                    action='ACCESS_DENIED',
                    resource_type='employee' if 'employee_id' in view_args else None,
                    resource_id=view_args.get('employee_id'),
                    result=RESULT_DENIED,
                    source=request.path,
                    purpose=permission,
                )
                status = 401 if str(error) == 'Authentication required' else 403
                return jsonify({'error': str(error)}), status
            return function(*args, **kwargs)
        return decorated
    return decorator


def require_employee_access(permission=PERMISSION_VIEW_EMPLOYEE):
    return require_permission(permission, resource_parameter='employee_id')


def require_operational_personnel_access():
    def decorator(function):
        @wraps(function)
        def decorated(*args, **kwargs):
            identity = g.get('authenticated_identity') or {}
            requested_id = kwargs.get('personnel_id')
            allowed = (
                identity.get('role') == ROLE_PERSONNEL
                and PERMISSION_VIEW_OWN_OPERATIONAL_RECORDS in permissions_for(identity)
                and identity.get('personnel_id') == requested_id
            ) or (
                identity.get('role') != ROLE_COMMANDER
                and PERMISSION_VIEW_OPERATIONAL_RECORDS in permissions_for(identity)
            )
            if not allowed:
                get_audit_service().record_event(
                    actor_user_id=identity.get('user_id'),
                    actor_role=identity.get('role'),
                    action='ACCESS_DENIED',
                    resource_type='operational_records',
                    resource_id=requested_id,
                    result=RESULT_DENIED,
                    source=request.path,
                    purpose='VIEW_OPERATIONAL_RECORDS',
                )
                return jsonify({'error': 'Forbidden'}), 403
            return function(*args, **kwargs)
        return decorated
    return decorator


def require_wellness_personnel_access():
    def decorator(function):
        @wraps(function)
        def decorated(*args, **kwargs):
            identity = g.get('authenticated_identity') or {}
            requested_id = kwargs.get('personnel_id')
            role = identity.get('role')
            staff_access = (
                role in {ROLE_WELFARE_OFFICER, ROLE_ADMIN}
                and PERMISSION_VIEW_WELLNESS_RECORDS in permissions_for(identity)
            )
            if not staff_access:
                get_audit_service().record_event(
                    actor_user_id=identity.get('user_id'),
                    actor_role=role,
                    action='WELLNESS_ACCESS_DENIED',
                    resource_type='wellness_assessment',
                    resource_id=requested_id,
                    result=RESULT_DENIED,
                    source=request.path,
                    purpose='View individual wellness assessments',
                )
                return jsonify({'error': 'Forbidden'}), 403
            return function(*args, **kwargs)
        return decorated
    return decorator
