"""
Authentication and Authorization
JWT-based authentication for API endpoints
Updated to use settings from settings.py
Replaced passlib with direct bcrypt usage to fix compatibility issues
"""

from datetime import datetime, timedelta
from typing import Optional, Dict
from jose import JWTError, jwt
# from passlib.context import CryptContext  <-- Removed to fix compatibility error
import bcrypt  # <-- Added direct bcrypt usage
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import os
import json
import logging

from app.core.settings import settings

logger = logging.getLogger(__name__)

# HTTP Bearer for token authentication
security = HTTPBearer(auto_error=False)  # auto_error=False allows optional auth


class AuthManager:
    """Manages user authentication and authorization"""
    
    def __init__(self):
        self.users_file = settings.USERS_FILE
        self.users = self._load_users()
        self.failed_attempts = {}  # Track failed login attempts
    
    def _load_users(self) -> Dict:
        """Load users from file"""
        if os.path.exists(self.users_file):
            try:
                with open(self.users_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading users: {e}")
                return {}
        return {}
    
    def _save_users(self):
        """Save users to file"""
        try:
            with open(self.users_file, 'w') as f:
                json.dump(self.users, f, indent=2)
            logger.info("Users saved successfully")
        except Exception as e:
            logger.error(f"Error saving users: {e}")
    
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify a password against a hash using bcrypt directly"""
        try:
            # Bcrypt requires bytes for both inputs
            if isinstance(hashed_password, str):
                hashed_bytes = hashed_password.encode('utf-8')
            else:
                hashed_bytes = hashed_password
            
            plain_bytes = plain_password.encode('utf-8')
            
            return bcrypt.checkpw(plain_bytes, hashed_bytes)
        except Exception as e:
            logger.error(f"Error verifying password: {e}")
            return False
    
    def get_password_hash(self, password: str) -> str:
        """Hash a password using bcrypt directly"""
        # Bcrypt has a max length of 72 bytes. 
        password_bytes = password.encode('utf-8')
        salt = bcrypt.gensalt()
        hashed_bytes = bcrypt.hashpw(password_bytes, salt)
        return hashed_bytes.decode('utf-8')
    
    def is_account_locked(self, username: str) -> bool:
        """Check if account is locked due to failed attempts"""
        if username not in self.failed_attempts:
            return False
        
        attempts_data = self.failed_attempts[username]
        if attempts_data["count"] >= settings.MAX_FAILED_LOGIN_ATTEMPTS:
            # Check if lockout period has expired
            lockout_time = datetime.fromisoformat(attempts_data["locked_until"])
            if datetime.utcnow() < lockout_time:
                return True
            else:
                # Lockout period expired, reset
                del self.failed_attempts[username]
                return False
        
        return False
    
    def record_failed_attempt(self, username: str):
        """Record a failed login attempt"""
        if username not in self.failed_attempts:
            self.failed_attempts[username] = {
                "count": 0,
                "locked_until": None
            }
        
        self.failed_attempts[username]["count"] += 1
        
        if self.failed_attempts[username]["count"] >= settings.MAX_FAILED_LOGIN_ATTEMPTS:
            lockout_until = datetime.utcnow() + timedelta(minutes=settings.LOCKOUT_DURATION_MINUTES)
            self.failed_attempts[username]["locked_until"] = lockout_until.isoformat()
            logger.warning(f"Account {username} locked until {lockout_until}")
    
    def reset_failed_attempts(self, username: str):
        """Reset failed login attempts on successful login"""
        if username in self.failed_attempts:
            del self.failed_attempts[username]
    
    def authenticate_user(self, username: str, password: str) -> Optional[Dict]:
        """Authenticate a user"""
        # Check if account is locked
        if self.is_account_locked(username):
            logger.warning(f"Login attempt for locked account: {username}")
            return None
        
        user = self.users.get(username)
        if not user:
            self.record_failed_attempt(username)
            return None
        
        if not self.verify_password(password, user["hashed_password"]):
            self.record_failed_attempt(username)
            return None
        
        # Successful authentication
        self.reset_failed_attempts(username)
        
        return {
            "username": username,
            "email": user.get("email", ""),
            "is_active": user.get("is_active", True),
            "created_at": user.get("created_at")
        }
    
    def create_access_token(self, data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """Create a JWT access token"""
        to_encode = data.copy()
        
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
        
        to_encode.update({
            "exp": expire,
            "iat": datetime.utcnow()
        })
        
        encoded_jwt = jwt.encode(
            to_encode, 
            settings.JWT_SECRET_KEY, 
            algorithm=settings.JWT_ALGORITHM
        )
        return encoded_jwt
    
    def decode_token(self, token: str) -> Optional[Dict]:
        """Decode and verify a JWT token"""
        try:
            payload = jwt.decode(
                token, 
                settings.JWT_SECRET_KEY, 
                algorithms=[settings.JWT_ALGORITHM]
            )
            username: str = payload.get("sub")
            if username is None:
                return None
            return {"username": username, "payload": payload}
        except JWTError as e:
            logger.debug(f"Token decode error: {e}")
            return None
    
    def create_user(self, username: str, password: str, email: str = "") -> tuple[bool, str]:
        """Create a new user"""
        if username in self.users:
            return False, "Username already exists"
        
        # Validate password strength
        is_valid, message = settings.validate_password_strength(password)
        if not is_valid:
            return False, message
        
        self.users[username] = {
            "hashed_password": self.get_password_hash(password),
            "email": email,
            "is_active": True,
            "created_at": datetime.utcnow().isoformat()
        }
        self._save_users()
        logger.info(f"User created: {username}")
        return True, "User created successfully"
    
    def get_user(self, username: str) -> Optional[Dict]:
        """Get user info"""
        user = self.users.get(username)
        if user:
            return {
                "username": username,
                "email": user.get("email", ""),
                "is_active": user.get("is_active", True),
                "created_at": user.get("created_at")
            }
        return None
    
    def update_user_password(self, username: str, new_password: str) -> tuple[bool, str]:
        """Update user password"""
        if username not in self.users:
            return False, "User not found"
        
        # Validate password strength
        is_valid, message = settings.validate_password_strength(new_password)
        if not is_valid:
            return False, message
        
        self.users[username]["hashed_password"] = self.get_password_hash(new_password)
        self._save_users()
        logger.info(f"Password updated for user: {username}")
        return True, "Password updated successfully"
    
    def delete_user(self, username: str) -> bool:
        """Delete a user"""
        if username in self.users:
            del self.users[username]
            self._save_users()
            logger.info(f"User deleted: {username}")
            return True
        return False


# Global auth manager instance
auth_manager = AuthManager()


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Optional[Dict]:
    """
    Dependency to get current authenticated user
    Returns None if not authenticated (allows optional auth)
    """
    if not credentials:
        return None
    
    token = credentials.credentials
    token_data = auth_manager.decode_token(token)
    
    if token_data is None:
        return None
    
    username = token_data.get("username")
    user = auth_manager.get_user(username)
    
    return user


async def require_auth(
    current_user: Optional[Dict] = Depends(get_current_user)
) -> Dict:
    """
    Dependency that REQUIRES authentication
    Use this for protected endpoints
    """
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not current_user.get("is_active", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user account"
        )
    
    return current_user


async def optional_auth(
    current_user: Optional[Dict] = Depends(get_current_user)
) -> Optional[Dict]:
    """
    Dependency for optional authentication
    Returns user if authenticated, None otherwise
    """
    return current_user


def create_initial_admin():
    """Create initial admin user if no users exist"""
    if not auth_manager.users:
        # Create default admin user from settings
        success, message = auth_manager.create_user(
            username=settings.ADMIN_USERNAME,
            password=settings.ADMIN_PASSWORD,
            email=settings.ADMIN_EMAIL
        )
        
        if success:
            logger.info("="*60)
            logger.info("✅ Initial admin user created")
            logger.info(f"   Username: {settings.ADMIN_USERNAME}")
            logger.info(f"   Password: {settings.ADMIN_PASSWORD}")
            logger.info("="*60)
            logger.warning("⚠️  CHANGE THE ADMIN PASSWORD IMMEDIATELY!")
            logger.info("="*60)
        else:
            logger.error(f"Failed to create admin user: {message}")

# Alias for clarity in downstream modules
get_current_active_user = require_auth