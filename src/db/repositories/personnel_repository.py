"""MongoDB-backed repository for the SIH personnel domain."""

from datetime import datetime, timezone

from src.schemas.personnel import normalize_personnel_record


class PersonnelRepository:
    """Persist core personnel records without embedding business logic."""

    def __init__(self, database=None, collection_name='personnel_identity', operations_collection_name='personnel_operations'):
        self.database = database
        self.collection = self._get_collection(database, collection_name)
        self.operations_collection = self._get_collection(database, operations_collection_name)
        if self.collection is not None and hasattr(self.collection, 'create_index'):
            self.collection.create_index('personnel_id', unique=True)
            self.collection.create_index('unit_id')
            self.collection.create_index('status')
            self.collection.create_index('updated_at')
            self.collection.create_index('pseudonymous_id')
            self.collection.create_index(
                [('external_source', 1), ('external_personnel_id', 1)],
                unique=True,
                sparse=True,
            )

    def _get_collection(self, database, collection_name):
        if database is None:
            return None
        aliases = (collection_name, 'personnel', 'personnel_identity', 'personnel_operations')
        if hasattr(database, 'get_collection'):
            for candidate in aliases:
                try:
                    value = database.get_collection(candidate)
                    if value is not None:
                        return value
                except Exception:
                    continue
        if hasattr(database, '__getitem__'):
            for candidate in aliases:
                try:
                    value = database[candidate]
                    if value is not None:
                        return value
                except (KeyError, TypeError):
                    continue
        for candidate in aliases:
            value = getattr(database, candidate, None)
            if value is not None:
                return value
        return None

    def _prepare_record(self, record):
        normalized = normalize_personnel_record(record)
        normalized.setdefault('status', 'ACTIVE')
        normalized['updated_at'] = datetime.now(timezone.utc).isoformat()
        if 'created_at' not in normalized:
            normalized['created_at'] = normalized['updated_at']
        return normalized

    def create(self, record):
        normalized = self._prepare_record(record)
        if self.collection is None:
            return normalized
        if self.collection.find_one({'personnel_id': normalized['personnel_id']}) is not None:
            raise ValueError(f"Personnel {normalized['personnel_id']} already exists")
        self.collection.insert_one(normalized)
        return normalized

    def get_by_personnel_id(self, personnel_id, include_inactive=False):
        if self.collection is None:
            return None
        existing = self.collection.find_one({'personnel_id': personnel_id})
        if existing is None or (not include_inactive and existing.get('status') == 'INACTIVE'):
            return None
        return existing

    def find_by_external_identity(self, external_source, external_personnel_id):
        if self.collection is None:
            return None
        return self.collection.find_one({
            'external_source': external_source,
            'external_personnel_id': external_personnel_id,
        })

    def next_personnel_id(self):
        """Allocate an internal ID without inventing an external identity."""
        if self.collection is None:
            return 'P001'
        highest = 0
        for record in self.collection.find():
            value = record.get('personnel_id', '')
            if isinstance(value, str) and value.startswith('P') and value[1:].isdigit():
                highest = max(highest, int(value[1:]))
        return f'P{highest + 1:03d}'

    def list(self):
        if self.collection is None:
            return []
        records = self.collection.find()
        return [record for record in records if record.get('status') != 'INACTIVE']

    def count_authorized(self):
        """Count active directory records without loading the directory."""
        if self.collection is None:
            return 0
        query = {'status': {'$ne': 'INACTIVE'}}
        try:
            return self.collection.count_documents(query)
        except AttributeError:
            return sum(1 for record in self.collection.find() if record.get('status') != 'INACTIVE')

    def list_directory(self, *, page, page_size, search=None, status=None, unit_id=None):
        """Return a bounded, deterministically ordered directory page.

        MongoDB performs filtering, counting, sorting, and pagination in the
        normal production path. The small fallback keeps the repository usable
        with the project's lightweight test collections.
        """
        if self.collection is None:
            return [], 0

        query = {'status': {'$ne': 'INACTIVE'}}
        if status is not None:
            query['status'] = status
        if unit_id is not None:
            query['unit_id'] = unit_id
        if search:
            import re
            pattern = re.escape(search)
            query['$or'] = [
                {'personnel_id': {'$regex': pattern, '$options': 'i'}},
                {'pseudonymous_id': {'$regex': pattern, '$options': 'i'}},
            ]

        try:
            total = self.collection.count_documents(query)
            cursor = self.collection.find(query).sort([('updated_at', -1), ('personnel_id', 1)])
            records = list(cursor.skip((page - 1) * page_size).limit(page_size))
        except (AttributeError, TypeError):
            records = [dict(record) for record in self.collection.find()]
            records = [record for record in records if self._directory_matches(record, search, status, unit_id)]
            records.sort(key=lambda record: (record.get('updated_at', ''), record.get('personnel_id', '')), reverse=True)
            total = len(records)
            start = (page - 1) * page_size
            records = records[start:start + page_size]

        return records, total

    @staticmethod
    def _directory_matches(record, search, status, unit_id):
        if record.get('status') == 'INACTIVE':
            return False
        if status is not None and record.get('status') != status:
            return False
        if unit_id is not None and record.get('unit_id') != unit_id:
            return False
        if search:
            needle = search.casefold()
            return needle in str(record.get('personnel_id', '')).casefold() or needle in str(record.get('pseudonymous_id', '')).casefold()
        return True

    def update(self, personnel_id, updates, include_inactive=False):
        if self.collection is None:
            return {'personnel_id': personnel_id, **updates}
        existing = self.get_by_personnel_id(personnel_id, include_inactive=include_inactive)
        if existing is None:
            raise ValueError(f"Personnel {personnel_id} not found")
        patch = dict(updates)
        if 'personnel_id' in patch:
            raise ValueError('personnel_id cannot be changed')
        patch['updated_at'] = datetime.now(timezone.utc).isoformat()
        self.collection.update_one({'personnel_id': personnel_id}, {'$set': patch})
        refreshed = self.get_by_personnel_id(personnel_id, include_inactive=include_inactive)
        if refreshed is None:
            return None
        return refreshed

    def deactivate(self, personnel_id):
        if self.collection is None:
            return {'personnel_id': personnel_id, 'status': 'INACTIVE'}
        existing = self.get_by_personnel_id(personnel_id)
        if existing is None:
            raise ValueError(f"Personnel {personnel_id} not found")
        self.collection.update_one({'personnel_id': personnel_id}, {'$set': {'status': 'INACTIVE', 'updated_at': datetime.now(timezone.utc).isoformat()}})
        return {'personnel_id': personnel_id, 'status': 'INACTIVE'}

    def delete(self, personnel_id):
        if self.collection is None:
            return {'personnel_id': personnel_id, 'deleted': True}
        existing = self.get_by_personnel_id(personnel_id)
        if existing is None:
            raise ValueError(f"Personnel {personnel_id} not found")
        self.collection.delete_one({'personnel_id': personnel_id})
        return {'personnel_id': personnel_id, 'deleted': True}
