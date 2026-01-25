"""
Redis Caching Layer
Provides a wrapper around Redis for caching API responses and data
"""
import json
import logging
import redis
from typing import Any, Optional, Dict
from app.core.settings import settings

logger = logging.getLogger(__name__)

class RedisCache:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(RedisCache, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self.enabled = False
        self.client = None
        
        # Check if Redis is enabled in settings or inferred from environment
        if settings.REDIS_HOST or settings.REDIS_ENABLED:
            try:
                # Initialize Redis client
                self.client = redis.Redis(
                    host=settings.REDIS_HOST or 'localhost',
                    port=settings.REDIS_PORT,
                    db=settings.REDIS_DB,
                    password=settings.REDIS_PASSWORD,
                    decode_responses=True, # Automatically decode bytes to strings
                    socket_connect_timeout=2,
                    socket_timeout=2
                )
                
                # Test connection
                self.client.ping()
                self.enabled = True
                logger.info(f"✅ Redis Cache initialized and connected to {settings.REDIS_HOST}:{settings.REDIS_PORT}")
                
            except Exception as e:
                logger.warning(f"⚠️ Redis Connection Failed: {e}. Caching will be disabled.")
                self.enabled = False
                self.client = None
        else:
            logger.info("ℹ️ Redis not configured. Caching is disabled.")
            
        self._initialized = True

    def get(self, key: str) -> Optional[Any]:
        """
        Retrieve a value from the cache
        """
        if not self.enabled or not self.client:
            return None
            
        try:
            data = self.client.get(key)
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            logger.warning(f"Cache GET error for key {key}: {e}")
            return None

    def set(self, key: str, value: Any, ttl: int = 300) -> bool:
        """
        Set a value in the cache with TTL (seconds)
        """
        if not self.enabled or not self.client:
            return False
            
        try:
            serialized = json.dumps(value)
            return self.client.setex(key, ttl, serialized)
        except Exception as e:
            logger.warning(f"Cache SET error for key {key}: {e}")
            return False

    def delete(self, key: str) -> bool:
        """
        Delete a key from the cache
        """
        if not self.enabled or not self.client:
            return False
            
        try:
            return self.client.delete(key) > 0
        except Exception as e:
            logger.warning(f"Cache DELETE error for key {key}: {e}")
            return False
            
    def delete_pattern(self, pattern: str) -> int:
        """
        Delete all keys matching a pattern (e.g., 'trades:user_123:*')
        """
        if not self.enabled or not self.client:
            return 0
            
        try:
            keys = self.client.keys(pattern)
            if keys:
                return self.client.delete(*keys)
            return 0
        except Exception as e:
            logger.warning(f"Cache DELETE PATTERN error for {pattern}: {e}")
            return 0

# Global Cache Instance
cache = RedisCache()
