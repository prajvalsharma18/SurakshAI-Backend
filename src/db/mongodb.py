"""Managed MongoDB client configuration and health checks."""

import atexit
from threading import Lock
from typing import Optional

from config import (
    get_database_name as _get_database_name,
    get_mongodb_timeout_ms,
    get_mongodb_tls,
    get_mongodb_uri as _get_mongodb_uri,
)

try:
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError
except ImportError:  # pragma: no cover - dependency is installed in project environment.
    MongoClient = None
    PyMongoError = Exception


_client_lock = Lock()
_clients = {}


def get_mongodb_uri() -> Optional[str]:
    return _get_mongodb_uri()


def get_database_name() -> str:
    return _get_database_name()


def create_mongo_client(uri: Optional[str] = None, timeout_ms: Optional[int] = None):
    if MongoClient is None:
        raise RuntimeError('pymongo is not installed')
    mongo_uri = uri or get_mongodb_uri()
    if not mongo_uri:
        return None
    applied_timeout = timeout_ms if timeout_ms is not None else get_mongodb_timeout_ms()
    key = (mongo_uri, applied_timeout)
    with _client_lock:
        client = _clients.get(key)
        if client is None:
            client_options = {'serverSelectionTimeoutMS': applied_timeout}
            tls = get_mongodb_tls()
            if tls is not None:
                client_options['tls'] = tls
            client = MongoClient(mongo_uri, **client_options)
            _clients[key] = client
        return client


def get_database(uri: Optional[str] = None, database_name: Optional[str] = None):
    mongo_uri = uri or get_mongodb_uri()
    if not mongo_uri:
        return None
    client = create_mongo_client(mongo_uri)
    if client is None:
        return None
    return client.get_database(database_name or get_database_name())


def get_database_health_status(uri: Optional[str] = None):
    configured_uri = uri or get_mongodb_uri()
    database_name = get_database_name()
    if not configured_uri:
        return {
            'status': 'APPLICATION_UP',
            'database': database_name,
            'database_connected': False,
            'credentials_exposed': False,
            'message': 'MongoDB configuration not enabled; application continues in development mode.',
        }

    try:
        client = create_mongo_client(configured_uri)
        if client is None:
            raise RuntimeError('Mongo client unavailable')
        client.admin.command('ping')
        return {
            'status': 'DATABASE_CONNECTED',
            'database': database_name,
            'database_connected': True,
            'credentials_exposed': False,
            'message': 'MongoDB connection verified.',
        }
    except (PyMongoError, RuntimeError, ValueError):
        return {
            'status': 'DATABASE_UNAVAILABLE',
            'database': database_name,
            'database_connected': False,
            'credentials_exposed': False,
            'message': 'MongoDB unavailable; feature endpoints should fail explicitly without exposing credentials.',
        }


def close_mongo_clients():
    """Close all shared clients during orderly application shutdown."""
    with _client_lock:
        clients = list(_clients.values())
        _clients.clear()
    for client in clients:
        client.close()


atexit.register(close_mongo_clients)
