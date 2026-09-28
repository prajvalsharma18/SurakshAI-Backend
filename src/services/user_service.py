"""User provisioning and MongoDB-backed authentication."""

from datetime import datetime, timezone
from uuid import uuid4

from werkzeug.security import check_password_hash, generate_password_hash

from src.schemas.user import (
    USER_ROLES,
    USER_STATUSES,
    public_user,
    validate_password,
    validate_user_role,
    validate_user_status,
    validate_username,
)
from src.schemas.seed import validate_seed_batch_id
from src.security.audit import RESULT_DENIED, RESULT_FAILURE, RESULT_SUCCESS, get_audit_service


class UserService:
    def __init__(self, repository, personnel_repository=None, audit_service=None):
        self.repository = repository
        self.personnel_repository = personnel_repository
        self.audit_service = audit_service or get_audit_service()

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def _validate_personnel_mapping(self, personnel_id):
        if personnel_id is None:
            return None
        if not self.personnel_repository:
            raise ValueError('personnel mapping validation is unavailable')
        record = self.personnel_repository.get_by_personnel_id(personnel_id)
        if record is None:
            raise ValueError('personnel_id must reference an existing active personnel record')
        return personnel_id

    @staticmethod
    def _identity(record):
        identity = {
            'user_id': record['user_id'],
            'username': record['username'],
            'role': record['role'],
        }
        if record.get('personnel_id'):
            identity['personnel_id'] = record['personnel_id']
        return identity

    def authenticate(self, username, password):
        user = self.repository.get_by_username(username)
        if not user or user.get('status') != 'ACTIVE' or not password:
            return None
        if not check_password_hash(user.get('password_hash', ''), password):
            return None
        return self._identity(user)

    def create_user(self, *, username, password, role, personnel_id=None, status='ACTIVE',
                    user_id=None, source='admin', actor=None, seed_batch_id=None):
        validate_username(username)
        validate_password(password)
        validate_user_role(role)
        validate_user_status(status)
        personnel_id = self._validate_personnel_mapping(personnel_id)
        now = self._now()
        record = {
            'user_id': user_id or f'user-{uuid4()}',
            'username': username,
            'role': role,
            'status': status,
            'password_hash': generate_password_hash(password),
            'created_at': now,
            'updated_at': now,
        }
        if personnel_id:
            record['personnel_id'] = personnel_id
        if seed_batch_id is not None:
            record['seed_batch_id'] = validate_seed_batch_id(seed_batch_id)
        created = self.repository.create(record)
        self.audit_service.record_event(
            action='USER_CREATED', resource_type='user', resource_id=created['user_id'],
            result=RESULT_SUCCESS, source=source, purpose='Create user account',
            actor_user_id=(actor or {}).get('user_id'),
            actor_role=(actor or {}).get('role'),
        )
        return public_user(created)

    def list_users(self, *, status=None, role=None):
        if status is not None:
            validate_user_status(status)
        if role is not None:
            validate_user_role(role)
        return [public_user(record) for record in self.repository.list(status=status, role=role)]

    def get_user(self, user_id):
        record = self.repository.get_by_user_id(user_id)
        return None if record is None else public_user(record)

    _UNSET = object()

    def update_user(self, user_id, *, password=None, role=None, personnel_id=_UNSET, status=None, actor=None):
        existing = self.repository.get_by_user_id(user_id)
        if existing is None:
            raise ValueError(f'User {user_id} not found')
        updates = {'updated_at': self._now()}
        if password is not None:
            validate_password(password)
            updates['password_hash'] = generate_password_hash(password)
        if role is not None:
            validate_user_role(role)
            updates['role'] = role
        if status is not None:
            validate_user_status(status)
            updates['status'] = status
        if personnel_id is not self._UNSET:
            if personnel_id is None:
                updates['personnel_id'] = None
            else:
                updates['personnel_id'] = self._validate_personnel_mapping(personnel_id)
        updated = self.repository.update(user_id, updates)
        self.audit_service.record_event(
            action=(
                'USER_DISABLED' if status == 'DISABLED'
                else 'USER_ENABLED' if status == 'ACTIVE'
                else 'USER_UPDATED'
            ),
            resource_type='user', resource_id=user_id,
            result=RESULT_SUCCESS, source='user_service.update_user', purpose='Update user account',
            actor_user_id=(actor or {}).get('user_id'),
            actor_role=(actor or {}).get('role'),
        )
        return public_user(updated)

    def bootstrap_development_users(self, definitions):
        created = 0
        for definition in definitions.values():
            if self.repository.get_by_username(definition['username']) is not None:
                continue
            personnel_id = definition.get('personnel_id')
            if personnel_id and self.personnel_repository:
                if self.personnel_repository.get_by_personnel_id(personnel_id) is None:
                    personnel_id = None
            self.create_user(
                username=definition['username'], password=definition['password'],
                role=definition['role'], personnel_id=personnel_id,
                user_id=definition['user_id'], source='development_bootstrap',
            )
            created += 1
        return created
