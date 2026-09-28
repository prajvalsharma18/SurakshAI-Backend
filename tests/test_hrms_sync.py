import unittest

from src.db.repositories.personnel_repository import PersonnelRepository
from src.integrations.hrms import NormalizedPayloadHRMSAdapter
from src.schemas.hrms import normalize_hrms_sync_request
from src.services.hrms_sync_service import HRMSSyncService
from src.services.personnel_service import PersonnelService


class FakeCollection:
    def __init__(self):
        self.records = []

    def create_index(self, *args, **kwargs):
        return None

    def find(self, query=None):
        return [dict(record) for record in self.records]

    def find_one(self, query):
        return next((dict(record) for record in self.records
                     if all(record.get(key) == value for key, value in query.items())), None)

    def insert_one(self, record):
        self.records.append(dict(record))

    def update_one(self, query, update):
        record = self.find_one(query)
        if record:
            for current in self.records:
                if current.get('personnel_id') == query.get('personnel_id'):
                    current.update(update.get('$set', {}))
                    return


class FakeDatabase:
    def __init__(self):
        self.personnel_identity = FakeCollection()
        self.personnel_operations = FakeCollection()
        self.users = FakeCollection()

    def get_collection(self, name):
        return getattr(self, name)


class FakeAudit:
    def __init__(self):
        self.events = []

    def record_event(self, **event):
        self.events.append(event)


def record(**changes):
    value = {
        'external_personnel_id': 'HRMS-84721', 'unit_id': 'UNIT-A', 'rank': 'Officer',
        'service_years': 5, 'posting_type': 'FIELD', 'status': 'ACTIVE',
    }
    value.update(changes)
    return value


class HRMSSyncTests(unittest.TestCase):
    def setUp(self):
        self.database = FakeDatabase()
        self.database.users.insert_one({
            'user_id': 'existing-user', 'username': 'existing-personnel',
            'role': 'PERSONNEL', 'personnel_id': 'P777',
        })
        self.repository = PersonnelRepository(self.database)
        self.audit = FakeAudit()
        self.personnel = PersonnelService(repository=self.repository, audit_service=self.audit)
        self.sync = HRMSSyncService(self.repository, self.personnel, self.audit)

    def run_sync(self, records, actor=None):
        return self.sync.sync(NormalizedPayloadHRMSAdapter(records), actor=actor)

    def test_create_update_idempotency_and_unchanged(self):
        first = self.run_sync([record()])
        self.assertEqual(first['created'], 1)
        personnel_id = first['results'][0]['personnel_id']
        second = self.run_sync([record()])
        self.assertEqual(second['unchanged'], 1)
        third = self.run_sync([record(unit_id='UNIT-B')])
        self.assertEqual(third['updated'], 1)
        self.assertEqual(self.repository.get_by_personnel_id(personnel_id)['unit_id'], 'UNIT-B')
        self.assertEqual(len(self.database.personnel_identity.records), 1)
        self.assertIsNone(self.repository.get_by_personnel_id('HRMS-84721'))

    def test_mapping_conflict_is_explicit_and_does_not_mutate(self):
        created = self.run_sync([record()])['results'][0]
        result = self.run_sync([record(personnel_id='P999', unit_id='UNIT-B')])
        self.assertEqual(len(result['conflicts']), 1)
        self.assertEqual(self.repository.get_by_personnel_id(created['personnel_id'])['unit_id'], 'UNIT-A')

    def test_deactivation_preserves_record_and_history_collections(self):
        created = self.run_sync([record()])['results'][0]
        result = self.run_sync([record(status='INACTIVE')])
        self.assertEqual(result['deactivated'], 1)
        self.assertEqual(self.repository.get_by_personnel_id(created['personnel_id'], include_inactive=True)['status'], 'INACTIVE')
        self.assertEqual(len(self.database.personnel_identity.records), 1)

    def test_validation_rejects_unknown_fields_and_oversized_batches(self):
        with self.assertRaises(ValueError):
            normalize_hrms_sync_request({'records': [record(raw_external_payload={'x': 1})]})
        with self.assertRaises(ValueError):
            normalize_hrms_sync_request({'records': [record()] * 501})
        with self.assertRaises(ValueError):
            normalize_hrms_sync_request({'records': [record()], 'unexpected': True})

    def test_existing_internal_record_can_be_mapped_without_changing_user_domain(self):
        self.personnel.create_personnel({
            'personnel_id': 'P777', 'unit_id': 'UNIT-A', 'rank': 'Officer',
            'service_years': 4, 'posting_type': 'FIELD', 'status': 'ACTIVE',
        })
        result = self.run_sync([record(personnel_id='P777')])
        self.assertEqual(result['updated'], 1)
        self.assertEqual(result['results'][0]['personnel_id'], 'P777')
        self.assertEqual(len(self.database.personnel_identity.records), 1)
        self.assertEqual(self.database.users.records, [{
            'user_id': 'existing-user', 'username': 'existing-personnel',
            'role': 'PERSONNEL', 'personnel_id': 'P777',
        }])

    def test_audit_is_actor_attributed_and_omits_source_payload(self):
        payload = record(raw_external_payload='should-not-be-accepted')
        with self.assertRaises(ValueError):
            self.run_sync([payload], actor={'user_id': 'admin-1', 'role': 'ADMIN'})
        self.run_sync([record()], actor={'user_id': 'admin-1', 'role': 'ADMIN'})
        event = self.audit.events[-1]
        self.assertEqual(event['actor_user_id'], 'admin-1')
        self.assertEqual(event['actor_role'], 'ADMIN')
        self.assertNotIn('external_personnel_id', event)
        self.assertNotIn('raw_external_payload', event)


if __name__ == '__main__':
    unittest.main()
