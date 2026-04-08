# core/lru_cache.py
from collections import OrderedDict
from typing import Any, Optional
import threading

class LRUCache:
    """Thread-safe LRU cache implementation."""
    
    def __init__(self, capacity: int = 50):
        self.capacity = capacity
        self.cache: OrderedDict = OrderedDict()
        self.lock = threading.Lock()
    
    def get(self, key) -> Optional[Any]:
        """Get an item from the cache."""
        with self.lock:
            if key in self.cache:
                # Move to end (most recently used)
                self.cache.move_to_end(key)
                return self.cache[key]
            return None
    
    def put(self, key, value) -> None:
        """Put an item in the cache."""
        with self.lock:
            if key in self.cache:
                # Update existing item
                self.cache.move_to_end(key)
                self.cache[key] = value
            else:
                # Add new item
                if len(self.cache) >= self.capacity:
                    # Remove least recently used
                    self.cache.popitem(last=False)
                self.cache[key] = value
    
    def remove(self, key) -> bool:
        """Remove an item from the cache."""
        with self.lock:
            if key in self.cache:
                del self.cache[key]
                return True
            return False
    
    def clear(self) -> None:
        """Clear all items from the cache."""
        with self.lock:
            self.cache.clear()
    
    def size(self) -> int:
        """Get the current size of the cache."""
        with self.lock:
            return len(self.cache)
    
    def keys(self):
        """Get all keys in the cache."""
        with self.lock:
            return list(self.cache.keys())