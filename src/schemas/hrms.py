"""Normalized HRMS synchronization schemas."""

from typing import TypedDict

from src.schemas.personnel import PERSONNEL_POSTING_TYPES, PERSONNEL_STATUSES, validate_personnel_identifier

MAX_HRMS_SYNC_BATCH_SIZE = 500
_ALLOWED_HRMS_FIELDS = {
    'external_personnel_id', 'external_source', 'personnel_id', 'unit_id',
    'rank', 'service_years', 'posting_type', 'status', 'external_updated_at',
}


class HRMSPersonnelRecord(TypedDict, total=False):
    external_personnel_id: str
    external_source: str
    personnel_id: str
    unit_id: str
    rank: str
    service_years: int
    posting_type: str
    status: str
    external_updated_at: str


class HRMSSyncRequest(TypedDict):
    records: list[HRMSPersonnelRecord]


class HRMSSyncItemResult(TypedDict, total=False):
    external_personnel_id: str
    personnel_id: str
    action: str
    reason: str


class HRMSSyncResult(TypedDict):
    source: str
    created: int
    updated: int
    unchanged: int
    deactivated: int
    conflicts: list[HRMSSyncItemResult]
    results: list[HRMSSyncItemResult]


def normalize_hrms_record(payload):
    if not isinstance(payload, dict):
        raise ValueError('Each HRMS personnel record must be an object')
    unexpected = set(payload) - _ALLOWED_HRMS_FIELDS
    if unexpected:
        raise ValueError(f'Unexpected HRMS field(s): {", ".join(sorted(unexpected))}')
    required = ('external_personnel_id', 'unit_id', 'rank', 'service_years', 'posting_type', 'status')
    missing = [field for field in required if field not in payload]
    if missing:
        raise ValueError(f'Missing required HRMS field(s): {", ".join(missing)}')
    record = dict(payload)
    external_id = record['external_personnel_id']
    if not isinstance(external_id, str) or not external_id.strip():
        raise ValueError('external_personnel_id must be a non-empty string')
    record['external_personnel_id'] = external_id.strip()
    source = record.get('external_source', 'HRMS')
    if not isinstance(source, str) or not source.strip():
        raise ValueError('external_source must be a non-empty string')
    record['external_source'] = source.strip()
    if 'personnel_id' in record and record['personnel_id'] is not None:
        record['personnel_id'] = validate_personnel_identifier(record['personnel_id'])
    if not isinstance(record['unit_id'], str) or not record['unit_id'].strip():
        raise ValueError('unit_id must be a non-empty string')
    if not isinstance(record['rank'], str) or not record['rank'].strip():
        raise ValueError('rank must be a non-empty string')
    if not isinstance(record['service_years'], int) or not 0 <= record['service_years'] <= 60:
        raise ValueError('service_years must be an integer between 0 and 60')
    if record['posting_type'] not in PERSONNEL_POSTING_TYPES:
        raise ValueError(f'posting_type must be one of {sorted(PERSONNEL_POSTING_TYPES)}')
    if record['status'] not in PERSONNEL_STATUSES:
        raise ValueError(f'status must be one of {sorted(PERSONNEL_STATUSES)}')
    if record.get('external_updated_at') is not None and not isinstance(record['external_updated_at'], str):
        raise ValueError('external_updated_at must be a string or null')
    return record


def normalize_hrms_sync_request(payload):
    if not isinstance(payload, dict) or set(payload) != {'records'} or not isinstance(payload.get('records'), list):
        raise ValueError('request must contain only a records list')
    if not payload['records']:
        raise ValueError('records must contain at least one personnel record')
    if len(payload['records']) > MAX_HRMS_SYNC_BATCH_SIZE:
        raise ValueError(f'records cannot exceed {MAX_HRMS_SYNC_BATCH_SIZE} items')
    return [normalize_hrms_record(record) for record in payload['records']]
