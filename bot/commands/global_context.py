from __future__ import annotations
from typing import TYPE_CHECKING, Dict, List, Type, TypeVar, Any
from dataclasses import dataclass, field

# 1. Define a generic TypeVar for our get/set methods
T = TypeVar('T')

@dataclass
class GlobalContext:
    # 2. Add a private dictionary to hold modular services/data
    _services: Dict[Type[Any], Any] = field(default_factory=dict)   
        
    # 3. Add methods to register and retrieve services by their type
    def register_service(self, service_type: Type[T], instance: T) -> None:
        """Registers a service for cross-module sharing."""
        self._services[service_type] = instance
        
    def get_service(self, service_type: Type[T]) -> T | None:
        """Retrieves a service. Returns None if not found."""
        return self._services.get(service_type)
        
    def require_service(self, service_type: Type[T]) -> T:
        """Retrieves a service, raising an error if it doesn't exist."""
        service = self.get_service(service_type)
        if service is None:
            raise RuntimeError(f"Required service {service_type.__name__} is not registered in GlobalContext")
        return service
        