"""Business operations and safe audit metadata for operational records."""

from datetime import date, timedelta

from src.security.audit import RESULT_SUCCESS, get_audit_service


class OperationalService:
    def __init__(self, repository, audit_service=None):
        self.repository = repository
        self.audit_service = audit_service or get_audit_service()

    @staticmethod
    def _date_filter(value, field):
        if value is None:
            return None

        if not isinstance(value, str):
            raise ValueError(f"{field} must use YYYY-MM-DD format")

        try:
            parsed = date.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f"{field} must use YYYY-MM-DD format") from error

        if parsed.isoformat() != value:
            raise ValueError(f"{field} must use YYYY-MM-DD format")

        return parsed.isoformat()

    def _audit(
        self,
        action,
        domain,
        record_id=None,
        identity=None,
    ):
        identity = identity or {}

        self.audit_service.record_event(
            actor_user_id=identity.get("user_id"),
            actor_role=identity.get("role"),
            action=action,
            resource_type=domain,
            resource_id=record_id,
            result=RESULT_SUCCESS,
            source="operational_service",
            purpose="Operational record management",
        )

    def create(
        self,
        domain,
        payload,
        identity=None,
        *,
        record_id=None,
        created_at=None,
        seed_batch_id=None,
    ):
        if {"record_id", "created_at"} & set(payload):
            raise ValueError(
                "record_id and created_at are assigned by the system"
            )

        repository_options = {}
        if record_id is not None:
            repository_options['record_id'] = record_id
        if created_at is not None:
            repository_options['created_at'] = created_at
        if seed_batch_id is not None:
            repository_options['seed_batch_id'] = seed_batch_id
        record = self.repository.create(domain, payload, **repository_options)

        self._audit(
            "OPERATIONAL_RECORD_CREATE",
            domain,
            record["record_id"],
            identity,
        )

        return record

    def get(self, domain, record_id, identity=None):
        record = self.repository.get(domain, record_id)

        if record is not None:
            self._audit(
                "OPERATIONAL_RECORD_VIEW",
                domain,
                record_id,
                identity,
            )

        return record

    def list(
        self,
        domain,
        *,
        personnel_id=None,
        start_date=None,
        end_date=None,
        identity=None,
    ):
        start_date = self._date_filter(start_date, "start_date")
        end_date = self._date_filter(end_date, "end_date")

        if start_date and end_date and start_date > end_date:
            raise ValueError(
                "start_date must not follow end_date"
            )

        records = self.repository.list(
            domain,
            personnel_id=personnel_id,
            start_date=start_date,
            end_date=end_date,
        )

        self._audit(
            "OPERATIONAL_RECORD_LIST",
            domain,
            identity=identity,
        )

        return records

    def update(self, domain, record_id, updates, identity=None):
        record = self.repository.update(
            domain,
            record_id,
            updates,
        )

        if record is not None:
            self._audit(
                "OPERATIONAL_RECORD_UPDATE",
                domain,
                record_id,
                identity,
            )

        return record

    def delete(self, domain, record_id, identity=None):
        deleted = self.repository.delete(
            domain,
            record_id,
        )

        if deleted:
            self._audit(
                "OPERATIONAL_RECORD_DELETE",
                domain,
                record_id,
                identity,
            )

        return deleted

    def aggregate_summary(
        self,
        *,
        start_date=None,
        end_date=None,
        identity=None,
    ):
        start_date = self._date_filter(start_date, "start_date")
        end_date = self._date_filter(end_date, "end_date")

        if start_date and end_date and start_date > end_date:
            raise ValueError(
                "start_date must not follow end_date"
            )

        if start_date is None:
            start_date = (
                date.today() - timedelta(days=30)
            ).isoformat()

        if end_date is None:
            end_date = date.today().isoformat()

        summary = {
            "start_date": start_date,
            "end_date": end_date,
            "domains": {},
        }

        for domain in self.repository.domains:
            records = self.repository.list(
                domain,
                start_date=start_date,
                end_date=end_date,
            )

            aggregate = {
                "record_count": len(records),
            }

            if domain == "duty_records":
                aggregate["average_duty_hours"] = (
                    round(
                        sum(
                            item["duty_hours"]
                            for item in records
                        ) / len(records),
                        2,
                    )
                    if records
                    else 0
                )

                aggregate["night_duty_records"] = sum(
                    item["night_duty"]
                    for item in records
                )

            elif domain == "workload_records":
                aggregate["average_workload_score"] = (
                    round(
                        sum(
                            item["workload_score"]
                            for item in records
                        ) / len(records),
                        2,
                    )
                    if records
                    else 0
                )

            summary["domains"][domain] = aggregate

        self._audit(
            "OPERATIONAL_SUMMARY_VIEW",
            "operational_summary",
            identity=identity,
        )

        return summary