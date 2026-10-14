import uuid

from pydantic import EmailStr, Field, field_validator

from app.schemas.common import ApiModel


class RegisterIn(ApiModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=2, max_length=60)

    @field_validator("password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        if not (any(c.isalpha() for c in v) and any(c.isdigit() for c in v)):
            raise ValueError("Password must contain at least one letter and one digit")
        return v


class LoginIn(ApiModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserOut(ApiModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: str


class TokenOut(ApiModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int
    user: UserOut
