"""Validation and public schemas for authenticated users."""

import re
from typing import TypedDict


USER_ROLES = {
    'PERSONNEL',
    'WELFARE_OFFICER',
    'COMMANDER',
    'ADMIN',
}
USER_STATUSES = {'ACTIVE', 'DISABLED'}


class UserIdentity(TypedDict, total=False):
    user_id: str
    username: str
    role: str
    personnel_id: str


class UserRecord(TypedDict, total=False):
    user_id: str
    username: str
    role: str
    status: str
    personnel_id: str
    password_hash: str
    created_at: str
    updated_at: str


def validate_username(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{3,64}', value):
        raise ValueError('username must contain 3-64 letters, numbers, dots, underscores, or hyphens')
    return value


def validate_user_role(value):
    if value not in USER_ROLES:
        raise ValueError(f'role must be one of {sorted(USER_ROLES)}')
    return value


def validate_user_status(value):
    if value not in USER_STATUSES:
        raise ValueError(f'status must be one of {sorted(USER_STATUSES)}')
    return value


def validate_password(value):
    if not isinstance(value, str) or len(value) < 12:
        raise ValueError('password must be at least 12 characters')
    return value


def public_user(record):
    allowed = ('user_id', 'username', 'role', 'status', 'personnel_id', 'created_at', 'updated_at')
    return {field: record[field] for field in allowed if field in record}
