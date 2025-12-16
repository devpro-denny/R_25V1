"""
Authentication Schemas
Pydantic models for auth requests/responses
"""

from pydantic import BaseModel, EmailStr
from typing import Optional


class UserLogin(BaseModel):
    """User login request"""
    username: str
    password: str


class UserRegister(BaseModel):
    """User registration request"""
    username: str
    password: str
    email: Optional[EmailStr] = ""


class Token(BaseModel):
    """JWT token response"""
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    """Token payload data"""
    username: Optional[str] = None


class User(BaseModel):
    """User information"""
    username: str
    email: Optional[str] = ""
    is_active: bool = True


class UserResponse(BaseModel):
    """User response with token"""
    user: User
    token: Token