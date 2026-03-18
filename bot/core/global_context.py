# pyright: reportAny=false, reportExplicitAny=false
from __future__ import annotations
from typing import Any

class GlobalContext:
    def __init__(self):
        self._services: dict[type[Any], Any] = {}
        
    def register_service[T](self, service_type: type[T], instance: T) -> None:
        """Registers a service for cross-module sharing."""
        self._services[service_type] = instance
        
    def get_service[T](self, service_type: type[T]) -> T | None:
        """Retrieves a service. Returns None if not found."""
        return self._services.get(service_type)
        
    def require_service[T](self, service_type: type[T]) -> T:
        """Retrieves a service, raising an error if it doesn't exist."""
        service = self.get_service(service_type)
        if service is None:
            raise RuntimeError(f"Required service {service_type.__name__} is not registered in GlobalContext")
        return service
        