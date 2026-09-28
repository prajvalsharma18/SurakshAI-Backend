"""Idempotent HRMS-to-personnel synchronization service."""

from datetime import datetime, timezone

from src.schemas.hrms import normalize_hrms_record
from src.security.audit import RESULT_FAILURE, RESULT_SUCCESS, get_audit_service


class HRMSSyncService:
    def __init__(self, personnel_repository, personnel_service, audit_service=None):
        self.personnel_repository = personnel_repository
        self.personnel_service = personnel_service
        self.audit_service = audit_service or get_audit_service()

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _comparable(record):
        fields = (
            'external_source', 'external_personnel_id', 'external_updated_at',
            'unit_id', 'rank', 'service_years', 'posting_type', 'status',
        )
        return {field: record.get(field) for field in fields}

    def _conflict(self, record, reason):
        return {
            'external_personnel_id': record['external_personnel_id'],
            'action': 'conflict',
            'reason': reason,
        }

    def sync(self, adapter, actor=None):
        records = [normalize_hrms_record(record) for record in adapter.fetch_personnel()]
        result = {
            'source': 'HRMS',
            'processed': 0,
            'created': 0,
            'updated': 0,
            'unchanged': 0,
            'deactivated': 0,
            'conflicts': [],
            'results': [],
        }
        seen = set()

        for record in records:
            external_key = (record['external_source'], record['external_personnel_id'])
            if external_key in seen:
                conflict = self._conflict(record, 'duplicate external identity in sync payload')
                result['conflicts'].append(conflict)
                continue
            seen.add(external_key)

            external_match = self.personnel_repository.find_by_external_identity(*external_key)
            internal_hint = record.get('personnel_id')
            internal_match = (
                self.personnel_repository.get_by_personnel_id(internal_hint, include_inactive=True)
                if internal_hint else None
            )

            if external_match and internal_hint and external_match['personnel_id'] != internal_hint:
                conflict = self._conflict(record, 'external identity maps to a different internal personnel_id')
                conflict['personnel_id'] = external_match['personnel_id']
                result['conflicts'].append(conflict)
                continue
            if internal_match:
                existing_source = internal_match.get('external_source')
                existing_external_id = internal_match.get('external_personnel_id')
                if (existing_source, existing_external_id) not in {(None, None), external_key}:
                    conflict = self._conflict(record, 'internal personnel_id is already mapped to another external identity')
                    conflict['personnel_id'] = internal_hint
                    result['conflicts'].append(conflict)
                    continue

            existing = external_match or internal_match
            now = self._now()
            desired = {
                'external_source': record['external_source'],
                'external_personnel_id': record['external_personnel_id'],
                'unit_id': record['unit_id'],
                'rank': record['rank'],
                'service_years': record['service_years'],
                'posting_type': record['posting_type'],
                'status': record['status'],
                'external_updated_at': record.get('external_updated_at'),
                'last_hrms_sync_at': now,
            }

            if existing is None:
                desired['personnel_id'] = self.personnel_repository.next_personnel_id()
                created = self.personnel_service.create_personnel(desired)
                result['created'] += 1
                item = {
                    'external_personnel_id': record['external_personnel_id'],
                    'personnel_id': created['personnel_id'],
                    'action': 'created',
                }
                if record['status'] == 'INACTIVE':
                    result['deactivated'] += 1
            else:
                if self._comparable(existing) == self._comparable(desired):
                    result['unchanged'] += 1
                    item = {
                        'external_personnel_id': record['external_personnel_id'],
                        'personnel_id': existing['personnel_id'],
                        'action': 'unchanged',
                    }
                else:
                    was_active = existing.get('status') != 'INACTIVE'
                    self.personnel_service.update_personnel_from_source(
                        existing['personnel_id'], desired, include_inactive=True,
                    )
                    result['updated'] += 1
                    if was_active and record['status'] == 'INACTIVE':
                        result['deactivated'] += 1
                    item = {
                        'external_personnel_id': record['external_personnel_id'],
                        'personnel_id': existing['personnel_id'],
                        'action': 'updated',
                    }
            result['results'].append(item)
            result['processed'] += 1

        self.audit_service.record_event(
            actor_user_id=(actor or {}).get('user_id'),
            actor_role=(actor or {}).get('role'),
            action='HRMS_SYNC_COMPLETED' if not result['conflicts'] else 'HRMS_SYNC_COMPLETED_WITH_CONFLICTS',
            resource_type='hrms_sync',
            result=RESULT_SUCCESS if not result['conflicts'] else RESULT_FAILURE,
            source='hrms_sync_service',
            purpose='Synchronize normalized HRMS personnel identities',
            created=result['created'], updated=result['updated'],
            unchanged=result['unchanged'], deactivated=result['deactivated'],
            conflict_count=len(result['conflicts']),
        )
        return result
