from pydantic import BaseModel, EmailStr, Field
from typing import Optional


class Token(BaseModel):
    sub: str
    email: Optional[EmailStr]
    name: Optional[str]
    username: Optional[str]  # map from 'cognito:username'
    iat: int  # issued at (UNIX timestamp)
    exp: int  # expiration (UNIX timestamp)
    aud: str  # audience / client_id
    plain: str # plain id_token


class User(BaseModel):
    username: str
    email: Optional[str]
    name: Optional[str]
    sub: str


class MessageDTO(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    message: str = Field(..., min_length=5, max_length=1000)
