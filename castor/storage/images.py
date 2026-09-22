from uuid import UUID

from castor.app import crud
from castor.app.db import User
from castor.storage.storage import ImageObject, ImageStorage


class DatabaseImageStorage(ImageStorage):

    async def save(self, byte_data: bytes, user: User | None = None) -> ImageObject:
        assert user, "User must be provided for saving images"
        assert byte_data, "Byte data must not be empty"

        m = await crud.save_user_image(user, byte_data)
        return ImageObject(
            id=str(m.unique_id),
            url=f"/assets/{m.unique_id}",
            owner=user.email,
        )

    async def get(self, uuid: str, user: User | None = None) -> ImageObject | None:
        assert user, "User must be provided for retrieving images"
        assert uuid, "UUID must not be empty"

        m = await crud.get_user_image(UUID(uuid), user)
        if not m:
            return None

        return ImageObject(
            id=str(m.unique_id),
            url=f"/assets/{m.id}",
            owner=user.email,
            blob=m.blob,
        )
