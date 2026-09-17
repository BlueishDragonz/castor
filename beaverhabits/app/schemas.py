import datetime
import uuid

from fastapi_users import schemas
from pydantic import BaseModel, model_validator


class UserRead(schemas.BaseUser[uuid.UUID]):
    pass


class UserCreate(schemas.BaseUserCreate):
    pass


class UserUpdate(schemas.BaseUserUpdate):
    """Public profile update, not a credential or recovery-address channel.

    Password changes must use security_actions; recovery-address changes need
    their own verified flow. Reject even explicit nulls rather than silently
    accepting a session-only request that appears to change a credential.
    """

    @model_validator(mode="before")
    @classmethod
    def reject_sensitive_fields(cls, value):
        if isinstance(value, dict) and {"password", "email"}.intersection(value):
            raise ValueError("Password and email changes require a dedicated verified flow")
        return value


class HabitCreate(BaseModel):
    name: str


class HabitRead(BaseModel):
    id: int
    name: str

    user: UserRead
    items: list["CheckedRecord"]


class CheckedRecord(BaseModel):
    id: str
    day: datetime.date
    done: bool
    habit: "HabitRead"
