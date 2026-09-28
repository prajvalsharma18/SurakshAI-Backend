"""Compatibility layer for legacy employee calls to the new SIH personnel foundation."""

from src.services.personnel_service import PersonnelService


class PersonnelCompatibilityAdapter:
    """Provide a migration-safe bridge from legacy employee records to the new personnel repository."""

    def __init__(self, repository=None, pseudonymizer=None):
        self.personnel_service = PersonnelService(repository=repository, pseudonymizer=pseudonymizer)

    def register_personnel(self, payload):
        if 'personnel_id' in payload:
            payload = dict(payload)
            payload['personnel_id'] = payload['personnel_id']
        return self.personnel_service.create_personnel(payload)

    def get_personnel(self, personnel_id):
        record = self.personnel_service.get_personnel(personnel_id)
        if record is None:
            raise KeyError('Personnel not found')
        return record

    def list_personnel(self):
        return {'total_employees': len(self.personnel_service.list_personnel()), 'employees': self.personnel_service.list_personnel()}
