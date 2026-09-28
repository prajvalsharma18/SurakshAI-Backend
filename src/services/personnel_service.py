"""Personnel service for SIH operational records with a repository abstraction."""

from src.security.audit import RESULT_SUCCESS, get_audit_service
from src.security.pseudonymization import Pseudonymizer
from src.schemas.seed import validate_seed_batch_id
from src.schemas.personnel import PersonnelDirectoryResponse


class PersonnelService:
    """Service layer that persists personnel records while preserving the legacy employee interface."""

    def __init__(self, repository=None, pseudonymizer=None, audit_service=None):
        self.repository = repository
        self.pseudonymizer = pseudonymizer or Pseudonymizer()
        self.audit_service = audit_service or get_audit_service()

    def create_personnel(self, record, *, seed_batch_id=None):
        payload = dict(record)
        if seed_batch_id is not None:
            payload['seed_batch_id'] = validate_seed_batch_id(seed_batch_id)
        payload['pseudonymous_id'] = self.pseudonymizer.pseudonymize(payload['personnel_id'])
        personnel = self.repository.create(payload)
        self.audit_service.record_event(
            action='PERSONNEL_CREATE',
            resource_type='personnel',
            resource_id=personnel['personnel_id'],
            result=RESULT_SUCCESS,
            source='personnel_service.create_personnel',
            purpose='Create SIH personnel record',
        )
        return personnel

    def get_personnel(self, personnel_id):
        record = self.repository.get_by_personnel_id(personnel_id)
        if record is None:
            return None
        self.audit_service.record_event(
            action='PERSONNEL_VIEW',
            resource_type='personnel',
            resource_id=personnel_id,
            result=RESULT_SUCCESS,
            source='personnel_service.get_personnel',
            purpose='Read personnel record',
        )
        return record

    def list_personnel(self):
        records = self.repository.list()
        self.audit_service.record_event(
            action='PERSONNEL_LIST',
            resource_type='personnel',
            result=RESULT_SUCCESS,
            source='personnel_service.list_personnel',
            purpose='List personnel records',
        )
        return records

    @staticmethod
    def can_view_directory(identity, personnel_id=None):
        role = (identity or {}).get('role')
        if role in {'WELFARE_OFFICER', 'ADMIN'}:
            return True
        return role == 'PERSONNEL' and personnel_id is not None and identity.get('personnel_id') == personnel_id

    @staticmethod
    def _directory_item(record):
        allowed = ('personnel_id', 'unit_id', 'status', 'posting_type', 'created_at', 'updated_at')
        item = {field: record[field] for field in allowed if field in record}
        if record.get('pseudonymous_id'):
            item['pseudonymous_reference'] = record['pseudonymous_id']
        return item

    def list_directory(self, identity, *, page, page_size, search=None, status=None, unit_id=None) -> PersonnelDirectoryResponse:
        records, total = self.repository.list_directory(
            page=page,
            page_size=page_size,
            search=search,
            status=status,
            unit_id=unit_id,
        )
        response = {
            'items': [self._directory_item(record) for record in records],
            'page': page,
            'page_size': page_size,
            'total': total,
        }
        identity = identity or {}
        self.audit_service.record_event(
            actor_user_id=identity.get('user_id'),
            actor_role=identity.get('role'),
            action='WELFARE_PERSONNEL_LIST_VIEW',
            resource_type='personnel_directory',
            result=RESULT_SUCCESS,
            source='personnel_service.list_directory',
            purpose='Discover authorized personnel for welfare workflows',
            page=page,
            page_size=page_size,
        )
        return response

    def get_directory(self, identity, personnel_id):
        if not self.can_view_directory(identity, personnel_id):
            return None
        record = self.repository.get_by_personnel_id(personnel_id)
        if record is None:
            return None
        identity = identity or {}
        self.audit_service.record_event(
            actor_user_id=identity.get('user_id'),
            actor_role=identity.get('role'),
            action='WELFARE_PERSONNEL_VIEW',
            resource_type='personnel_directory',
            resource_id=personnel_id,
            result=RESULT_SUCCESS,
            source='personnel_service.get_directory',
            purpose='View authorized personnel directory record',
        )
        return self._directory_item(record)

    def update_personnel(self, personnel_id, updates, include_inactive=False):
        payload = dict(updates)
        if 'personnel_id' in payload:
            raise ValueError('personnel_id cannot be updated')
        protected = {'external_source', 'external_personnel_id', 'external_updated_at', 'last_hrms_sync_at'}
        if protected.intersection(payload):
            raise ValueError('External identity and synchronization metadata may only be changed by HRMS synchronization')
        updated = self.repository.update(personnel_id, payload, include_inactive=include_inactive)
        self.audit_service.record_event(
            action='PERSONNEL_UPDATE',
            resource_type='personnel',
            resource_id=personnel_id,
            result=RESULT_SUCCESS,
            source='personnel_service.update_personnel',
            purpose='Update personnel metadata',
        )
        return updated

    def update_personnel_from_source(self, personnel_id, updates, include_inactive=False):
        """Apply an already-validated source synchronization update."""
        payload = dict(updates)
        if 'personnel_id' in payload:
            raise ValueError('personnel_id cannot be updated')
        updated = self.repository.update(personnel_id, payload, include_inactive=include_inactive)
        self.audit_service.record_event(
            action='PERSONNEL_UPDATE',
            resource_type='personnel',
            resource_id=personnel_id,
            result=RESULT_SUCCESS,
            source='personnel_service.update_personnel_from_source',
            purpose='Synchronize source-owned personnel metadata',
        )
        return updated

    def deactivate_personnel(self, personnel_id):
        result = self.repository.deactivate(personnel_id)
        self.audit_service.record_event(
            action='PERSONNEL_DELETE',
            resource_type='personnel',
            resource_id=personnel_id,
            result=RESULT_SUCCESS,
            source='personnel_service.deactivate_personnel',
            purpose='Deactivate personnel record',
        )
        return result
