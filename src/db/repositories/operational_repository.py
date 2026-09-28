"""MongoDB repository for the Phase 3B operational record domains."""

from src.schemas.operational import OPERATIONAL_SCHEMAS, normalize_operational_record


class OperationalRepository:
    def __init__(self, database=None):
        self.database = database
        self.collections = {}
        if database is None:
            return
        for domain, schema in OPERATIONAL_SCHEMAS.items():
            collection = database.get_collection(domain) if hasattr(database, 'get_collection') else database[domain]
            collection.create_index('record_id', unique=True)
            collection.create_index([('personnel_id', 1), (schema['date_field'], -1)])
            self.collections[domain] = collection

    @property
    def domains(self):
        return tuple(OPERATIONAL_SCHEMAS)

    def _collection(self, domain):
        if domain not in OPERATIONAL_SCHEMAS:
            raise ValueError('Unknown operational record domain')
        collection = self.collections.get(domain)
        if collection is None:
            raise RuntimeError('Operational database is unavailable')
        return collection

    def create(self, domain, payload, *, record_id=None, created_at=None, seed_batch_id=None):
        collection = self._collection(domain)
        if seed_batch_id is not None:
            payload = {**payload, 'seed_batch_id': seed_batch_id}
        record = normalize_operational_record(domain, payload, record_id=record_id, created_at=created_at)
        collection.insert_one(record)
        return record

    def get(self, domain, record_id):
        record = self._collection(domain).find_one({'record_id': record_id})
        return self._without_mongo_id(record)

    def list(self, domain, *, personnel_id=None, start_date=None, end_date=None):
        collection = self._collection(domain)
        filter_document = {}
        if personnel_id is not None:
            filter_document['personnel_id'] = personnel_id
        date_field = OPERATIONAL_SCHEMAS[domain]['date_field']
        date_filter = {}
        if start_date is not None:
            date_filter['$gte'] = start_date
        if end_date is not None:
            date_filter['$lte'] = end_date
        if date_filter:
            filter_document[date_field] = date_filter
        return [self._without_mongo_id(record) for record in collection.find(filter_document).sort(date_field, -1)]

    @staticmethod
    def _without_mongo_id(record):
        if record is None:
            return None
        return {key: value for key, value in record.items() if key != '_id'}

    def update(self, domain, record_id, updates):
        collection = self._collection(domain)
        existing = self._without_mongo_id(collection.find_one({'record_id': record_id}))
        if existing is None:
            return None
        if {'record_id', 'created_at'} & set(updates):
            raise ValueError('record_id and created_at cannot be changed')
        if updates.get('personnel_id', existing['personnel_id']) != existing['personnel_id']:
            raise ValueError('personnel_id cannot be changed')
        replacement = {**existing, **updates}
        normalized = normalize_operational_record(
            domain,
            replacement,
            record_id=record_id,
            created_at=existing['created_at'],
        )
        collection.update_one({'record_id': record_id}, {'$set': normalized})
        return normalized

    def delete(self, domain, record_id):
        result = self._collection(domain).delete_one({'record_id': record_id})
        return result.deleted_count > 0