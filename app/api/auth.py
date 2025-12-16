"""
Authentication API Endpoints
Login, register, token refresh, user management
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import timedelta

from app.core.auth import (
    auth_manager,
    require_auth,
    optional_auth
)
from app.core.settings import settings

router = APIRouter()


# ============================================================================
# Request/Response Models
# ============================================================================

class LoginRequest(BaseModel):
    """Login request model"""
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=settings.MIN_PASSWORD_LENGTH)


class RegisterRequest(BaseModel):
    """User registration request model"""
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=settings.MIN_PASSWORD_LENGTH)
    email: Optional[EmailStr] = None


class ChangePasswordRequest(BaseModel):
    """Change password request model"""
    current_password: str
    new_password: str = Field(..., min_length=settings.MIN_PASSWORD_LENGTH)


class TokenResponse(BaseModel):
    """Token response model"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    """User information response model"""
    username: str
    email: Optional[str]
    is_active: bool
    created_at: Optional[str]


class MessageResponse(BaseModel):
    """Generic message response"""
    message: str
    success: bool = True


# ============================================================================
# Public Endpoints (No Authentication Required)
# ============================================================================

@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    """
    Authenticate user and return access token
    
    **No authentication required**
    """
    # Check if authentication is enabled
    if not settings.ENABLE_AUTHENTICATION:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is disabled"
        )
    
    # Authenticate user
    user = auth_manager.authenticate_user(request.username, request.password)
    
    if not user:
        # Check if account is locked
        if auth_manager.is_account_locked(request.username):
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail=f"Account locked due to too many failed attempts. Try again in {settings.LOCKOUT_DURATION_MINUTES} minutes."
            )
        
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Check if user is active
    if not user.get("is_active", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive"
        )
    
    # Create access token
    access_token = auth_manager.create_access_token(
        data={"sub": user["username"]},
        expires_delta=timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    
    return TokenResponse(
        access_token=access_token,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )


@router.post("/register", response_model=MessageResponse)
async def register(request: RegisterRequest):
    """
    Register a new user account
    
    **No authentication required** (but can be restricted in production)
    """
    # In production, you might want to disable public registration
    # or require an invite code
    
    if not settings.ENABLE_AUTHENTICATION:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is disabled"
        )
    
    # Create user
    success, message = auth_manager.create_user(
        username=request.username,
        password=request.password,
        email=request.email or ""
    )
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message
        )
    
    return MessageResponse(
        message=message,
        success=True
    )


# ============================================================================
# Protected Endpoints (Authentication Required)
# ============================================================================

@router.get("/me", response_model=UserResponse)
async def get_current_user_info(current_user: dict = Depends(require_auth)):
    """
    Get current authenticated user information
    
    **Requires authentication**
    """
    return UserResponse(**current_user)


@router.post("/change-password", response_model=MessageResponse)
async def change_password(
    request: ChangePasswordRequest,
    current_user: dict = Depends(require_auth)
):
    """
    Change current user's password
    
    **Requires authentication**
    """
    # Verify current password
    user = auth_manager.authenticate_user(
        current_user["username"],
        request.current_password
    )
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect"
        )
    
    # Update password
    success, message = auth_manager.update_user_password(
        current_user["username"],
        request.new_password
    )
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message
        )
    
    return MessageResponse(
        message=message,
        success=True
    )


@router.post("/logout", response_model=MessageResponse)
async def logout(current_user: dict = Depends(require_auth)):
    """
    Logout current user (client should discard the token)
    
    **Requires authentication**
    """
    # Since we're using stateless JWT, actual logout happens on the client
    # by discarding the token. This endpoint is here for completeness.
    
    return MessageResponse(
        message="Logged out successfully",
        success=True
    )


# ============================================================================
# Status Endpoint
# ============================================================================

@router.get("/status")
async def auth_status():
    """
    Get authentication system status
    
    **No authentication required**
    """
    return {
        "authentication_enabled": settings.ENABLE_AUTHENTICATION,
        "registration_enabled": True,  # Can be controlled by settings
        "jwt_expiration_minutes": settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
        "password_requirements": {
            "min_length": settings.MIN_PASSWORD_LENGTH,
            "require_uppercase": settings.REQUIRE_PASSWORD_UPPERCASE,
            "require_lowercase": settings.REQUIRE_PASSWORD_LOWERCASE,
            "require_digits": settings.REQUIRE_PASSWORD_DIGITS,
            "require_special": settings.REQUIRE_PASSWORD_SPECIAL
        }
    }