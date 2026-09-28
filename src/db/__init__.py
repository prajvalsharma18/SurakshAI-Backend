"""Database access layer for the SIH personnel foundation."""

from .mongodb import create_mongo_client, get_database, get_database_health_status

__all__ = [
    'create_mongo_client',
    'get_database',
    'get_database_health_status',
]
