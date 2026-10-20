"""Explicit identity contracts; passwords and invitation secrets are redacted."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, SecretStr


def _utf8_password(value: SecretStr) -> SecretStr:
    try:
        value.get_secret_value().encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Password must contain valid Unicode") from None
    return value


Password = Annotated[SecretStr, AfterValidator(_utf8_password)]


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=254)
    password: Password = Field(min_length=12, max_length=128)
    invite_token: SecretStr = Field(min_length=43, max_length=43)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=254)
    password: Password = Field(min_length=1, max_length=128)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    email: str = Field(validation_alias="email_normalized")
    created_at: datetime


class LoginResponse(BaseModel):
    user: UserResponse
    csrf_token: str


class CsrfResponse(BaseModel):
    csrf_token: str
