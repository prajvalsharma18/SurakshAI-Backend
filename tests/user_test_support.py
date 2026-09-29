"""Shared persistent-user fixture for API regression tests."""

import os

from src.db.repositories.user_repository import UserRepository
from src.security.demo_users import DEVELOPMENT_USERS
from src.services.user_service import UserService


class _Collection:
    def __init__(self):
        self.records = []

    def create_index(self, *args, **kwargs):
        return None

    def find_one(self, query):
        for record in self.records:
            if all(record.get(key) == value for key, value in query.items()):
                return dict(record)
        return None

    def find(self, query=None):
        query = query or {}
        return [dict(record) for record in self.records if all(record.get(key) == value for key, value in query.items())]

    def insert_one(self, record):
        self.records.append(dict(record))

    def update_one(self, query, update):
        for record in self.records:
            if all(record.get(key) == value for key, value in query.items()):
                record.update(update.get('$set', {}))
                return


class _Database:
    def __init__(self):
        self.collection = _Collection()

    def get_collection(self, name):
        return self.collection


class _PersonnelLookup:
    def __init__(self, personnel_ids):
        self.personnel_ids = set(personnel_ids)

    def get_by_personnel_id(self, personnel_id):
        if personnel_id not in self.personnel_ids:
            return None
        return {'personnel_id': personnel_id, 'status': 'ACTIVE'}


def install_test_users(api_server, personnel_ids=('P001', 'P002', 'P003', 'P900')):
    os.environ.setdefault('JWT_SECRET_KEY', 'user-provisioning-test-secret')
    database = _Database()
    service = UserService(
        UserRepository(database),
        _PersonnelLookup(personnel_ids),
        api_server.audit_service,
    )
    for definition in DEVELOPMENT_USERS.values():
        service.create_user(
            username=definition['username'],
            password=definition['password'],
            role=definition['role'],
            personnel_id=definition.get('personnel_id'),
            user_id=definition['user_id'],
            source='test_bootstrap',
        )
    api_server.user_service = service
    api_server._development_bootstrap_complete = True
    return service
