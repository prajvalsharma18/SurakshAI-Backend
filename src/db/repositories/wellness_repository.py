"""MongoDB repository for isolated voluntary wellness assessments."""


class DuplicateWellnessAssessment(ValueError):
    """Raised when a personnel member already submitted for an assessment date."""


class WellnessRepository:
    COLLECTION_NAME = 'wellness_assessments'

    def __init__(self, database=None):
        self.database = database
        self.collection = None
        if database is None:
            return
        self.collection = (
            database.get_collection(self.COLLECTION_NAME)
            if hasattr(database, 'get_collection') else database[self.COLLECTION_NAME]
        )
        self.collection.create_index('assessment_id', unique=True)
        self.collection.create_index(
            [('personnel_id', 1), ('assessment_date', 1)],
            unique=True,
        )

    def _require_collection(self):
        if self.collection is None:
            raise RuntimeError('Wellness data store is unavailable')
        return self.collection

    @staticmethod
    def _without_mongo_id(record):
        if record is None:
            return None
        return {key: value for key, value in record.items() if key != '_id'}

    def create(self, assessment):
        collection = self._require_collection()
        duplicate = collection.find_one({
            'personnel_id': assessment['personnel_id'],
            'assessment_date': assessment['assessment_date'],
        })
        if duplicate is not None:
            raise DuplicateWellnessAssessment('An assessment already exists for this personnel member and date')
        try:
            collection.insert_one(assessment)
        except Exception as error:
            if error.__class__.__name__ == 'DuplicateKeyError':
                raise DuplicateWellnessAssessment(
                    'An assessment already exists for this personnel member and date'
                ) from error
            raise
        return dict(assessment)

    def list_for_personnel(self, personnel_id, *, start_date=None, end_date=None):
        collection = self._require_collection()
        filter_document = {'personnel_id': personnel_id}
        date_filter = {}
        if start_date is not None:
            date_filter['$gte'] = start_date
        if end_date is not None:
            date_filter['$lte'] = end_date
        if date_filter:
            filter_document['assessment_date'] = date_filter
        records = collection.find(filter_document).sort('assessment_date', -1)
        return [self._without_mongo_id(record) for record in records]

    def get_owned(self, personnel_id, assessment_id):
        record = self._require_collection().find_one({
            'personnel_id': personnel_id,
            'assessment_id': assessment_id,
        })
        return self._without_mongo_id(record)

    def delete_owned(self, personnel_id, assessment_id):
        result = self._require_collection().delete_one({
            'personnel_id': personnel_id,
            'assessment_id': assessment_id,
        })
        return result.deleted_count > 0