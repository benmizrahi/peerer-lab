"""
Storage factory for Peerer Broker Server.
Initializes and provides the configured StorageBackend instance.
"""

import logging
from app.config import settings
from app.storage.base import StorageBackend
from app.storage.memory import MemoryStorage

logger = logging.getLogger("peerer-broker")

_storage_instance = None

def get_storage() -> StorageBackend:
    global _storage_instance
    if _storage_instance is None:
        if settings.STORAGE_BACKEND == "firestore":
            try:
                from app.storage.firestore import FirestoreStorage
                _storage_instance = FirestoreStorage()
            except Exception as e:
                logger.error("Failed to initialize FirestoreStorage: %s. Falling back to MemoryStorage", e)
                _storage_instance = MemoryStorage()
        else:
            _storage_instance = MemoryStorage()
    return _storage_instance
