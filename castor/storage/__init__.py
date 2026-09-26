from castor.configs import StorageType, settings
from castor.storage.dict import DictHabitList
from castor.storage.images import DatabaseImageStorage
from castor.storage.session_memory import SessionDictStorage
from castor.storage.storage import SessionStorage, UserStorage
from castor.storage.user_db import UserDatabaseStorage
from castor.storage.user_file import UserDiskStorage

session_storage = SessionDictStorage()
user_disk_storage = UserDiskStorage()
user_database_storage = UserDatabaseStorage()
sqlite_storage = None

# TODO: retrieve image storage backend for each user
image_storage = DatabaseImageStorage()


def get_sessions_storage() -> SessionStorage:
    return session_storage


def get_user_dict_storage() -> UserStorage[DictHabitList]:
    if settings.HABITS_STORAGE == StorageType.USER_DISK:
        return user_disk_storage

    if settings.HABITS_STORAGE == StorageType.USER_DATABASE:
        return user_database_storage

    raise NotImplementedError("Storage type not implemented")
