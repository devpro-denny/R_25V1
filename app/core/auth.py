"""
Authentication and Authorization (Supabase)
Verifies Supabase JWT tokens and checks user approval status
"""

from typing import Optional, Dict
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import logging
from supabase.client import Client

from app.core.settings import settings
from app.core.supabase import supabase
from app.core.cache import cache

logger = logging.getLogger(__name__)

# HTTP Bearer for token extraction
security = HTTPBearer(auto_error=False)

async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Optional[Dict]:
    """
    Dependency to get current authenticated user via Supabase
    Returns None if not authenticated (allows optional auth)
    """
    if not credentials:
        return None
    
    token = credentials.credentials
    
    try:
        # Verify user with Supabase Auth
        user_response = supabase.auth.get_user(token)
        user = user_response.user
        
        if not user:
            return None

        # --- Auto-Promotion Logic ---
        # Check if this user matches the INITIAL_ADMIN_EMAIL
        if settings.INITIAL_ADMIN_EMAIL and user.email == settings.INITIAL_ADMIN_EMAIL:
             # Check if we need to promote (optimization: check local var first, but DB is source of truth)
             pass 
             # We will do the promotion check after fetching the profile to avoid redundant writes

        # Check profiles table for status (with short cache to reduce DB chatter)
        profile_cache_key = f"auth_profile:{user.id}"
        profile = cache.get(profile_cache_key)
        if profile is None:
            profile_response = (
                supabase.table("profiles")
                .select("role,is_approved,created_at")
                .eq("id", user.id)
                .single()
                .execute()
            )
            profile = profile_response.data or {}
            if profile:
                cache.set(profile_cache_key, profile, ttl=60)
        
        role = 'user'
        is_approved = False
        created_at = user.created_at # Fallback

        if profile:
            role = profile.get('role', 'user')
            is_approved = profile.get('is_approved', False)
            created_at = profile.get('created_at')

            # Check if we need to auto-promote NOW
            if (settings.INITIAL_ADMIN_EMAIL and 
                user.email == settings.INITIAL_ADMIN_EMAIL and 
                (role != 'admin' or not is_approved)):
                
                logger.info(f"Auto-promoting initial admin: {user.email}")
                supabase.table('profiles').update({
                    'role': 'admin',
                    'is_approved': True
                }).eq('id', user.id).execute()
                cache.delete(profile_cache_key)
                
                # Update local vars to reflect change
                role = 'admin'
                is_approved = True

        else:
            # Profile doesn't exist yet (maybe trigger failed or race condition)
            logger.warning(f"Profile missing for user {user.id}")
            
            # If it's the admin email, we might want to CREATE the profile or just return approved state
            # Ideally the trigger handles creation. If we are here, something might be slow.
            # But we can still enforce the logic.
            if settings.INITIAL_ADMIN_EMAIL and user.email == settings.INITIAL_ADMIN_EMAIL:
                 role = 'admin'
                 is_approved = True
                 # We could optionally insert here, but let's assume the trigger or next call fixes it.
                 # Actually, for robustness, if the trigger failed, we are in a weird state.
                 # Let's just rely on the 'user' default.

        return {
            "id": user.id,
            "email": user.email,
            "role": role,
            "is_approved": is_approved,
            "created_at": created_at
        }
        
    except Exception as e:
        logger.warning(f"Auth error: {e}")
        return None


async def require_login(
    current_user: Optional[Dict] = Depends(get_current_user)
) -> Dict:
    """
    Dependency that requires a valid JWT but IGNORES approval status.
    Used for /auth/me to allow unapproved users to check their status.
    """
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return current_user


async def require_auth(
    current_user: Dict = Depends(require_login)
) -> Dict:
    """
    Dependency that REQUIRES authentication AND Admin Approval.
    Used for all protected bot/trade routes.
    """
    if not current_user.get("is_approved", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account not approved by admin. Please contact support."
        )
    
    return current_user


async def optional_auth(
    current_user: Optional[Dict] = Depends(get_current_user)
) -> Optional[Dict]:
    """
    Dependency for optional authentication
    """
    return current_user

# Alias for compatibility
get_current_active_user = require_auth


