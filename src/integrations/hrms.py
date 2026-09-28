"""HRMS synchronization boundary; no network client is implemented here."""

from abc import ABC, abstractmethod

from src.schemas.hrms import normalize_hrms_sync_request


class HRMSAdapter(ABC):
    """Stable boundary for a future external HRMS connector."""

    @abstractmethod
    def fetch_personnel(self):
        raise NotImplementedError


class NormalizedPayloadHRMSAdapter(HRMSAdapter):
    """Controlled adapter for already-normalized test/import payloads."""

    def __init__(self, records):
        self._records = normalize_hrms_sync_request({'records': records})

    def fetch_personnel(self):
        return [dict(record) for record in self._records]
