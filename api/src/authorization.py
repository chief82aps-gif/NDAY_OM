"""
Authorization decorators and middleware for FastAPI routes

Provides role-based access control for endpoint protection.
"""

from functools import wraps
from fastapi import HTTPException, status, Depends, Header
from typing import List, Optional, Callable
import jwt
from api.src.permissions import Permission, Role, get_permissions, has_permission


def get_current_user_role(authorization: Optional[str] = Header(None)) -> str:
    """
    Extract and validate JWT token from Authorization header, return user role.
    
    Expected header: Authorization: Bearer <token>
    Expected JWT payload: {"role": "admin|manager|dispatcher|driver", ...}
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header"
        )
    
    try:
        # Extract token from "Bearer <token>"
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication scheme"
            )
        
        # Decode token (without signature verification for dev - use proper verification in prod)
        payload = jwt.decode(token, options={"verify_signature": False})
        role = payload.get("role")
        
        if not role:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token missing role claim"
            )
        
        # Validate role exists
        try:
            Role(role)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid role: {role}"
            )
        
        return role
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format"
        )
    except jwt.DecodeError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e)
        )


def require_role(*allowed_roles: str) -> Callable:
    """
    Decorator to require specific roles for endpoint access.
    
    Usage:
        @router.get("/financial-report")
        @require_role("admin", "manager")
        def get_financial_report(role: str = Depends(get_current_user_role)):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, role: str = None, **kwargs):
            if role not in allowed_roles:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"This resource requires one of: {', '.join(allowed_roles)}"
                )
            return await func(*args, role=role, **kwargs) if hasattr(func, '__await__') else func(*args, role=role, **kwargs)
        return wrapper
    return decorator


def require_permission(*permissions: Permission) -> Callable:
    """
    Decorator to require specific permission for endpoint access.
    Checks if user role has any of the specified permissions.
    
    Usage:
        @router.get("/invoices")
        @require_permission(Permission.VIEW_VARIABLE_INVOICES)
        def get_invoices(role: str = Depends(get_current_user_role)):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, role: str = None, **kwargs):
            user_permissions = get_permissions(role)
            if not any(perm in user_permissions for perm in permissions):
                perm_names = ", ".join([p.value for p in permissions])
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Insufficient permissions. Required: {perm_names}"
                )
            return await func(*args, role=role, **kwargs) if hasattr(func, '__await__') else func(*args, role=role, **kwargs)
        return wrapper
    return decorator


def require_permission_all(*permissions: Permission) -> Callable:
    """
    Decorator to require ALL specified permissions.
    
    Usage:
        @router.post("/update-financial")
        @require_permission_all(Permission.VIEW_FINANCIAL, Permission.MANAGE_ASSIGNMENTS)
        def update_financial(role: str = Depends(get_current_user_role)):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, role: str = None, **kwargs):
            user_permissions = get_permissions(role)
            if not all(perm in user_permissions for perm in permissions):
                perm_names = ", ".join([p.value for p in permissions])
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Insufficient permissions. All required: {perm_names}"
                )
            return await func(*args, role=role, **kwargs) if hasattr(func, '__await__') else func(*args, role=role, **kwargs)
        return wrapper
    return decorator


def require_admin(role: str = Depends(get_current_user_role)) -> str:
    """Dependency to ensure only admins access endpoint"""
    if role != Role.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return role


def require_admin_or_manager(role: str = Depends(get_current_user_role)) -> str:
    """Dependency to ensure admin or manager access"""
    if role not in {Role.ADMIN.value, Role.MANAGER.value}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or Manager access required"
        )
    return role


def require_financial_access(role: str = Depends(get_current_user_role)) -> str:
    """Dependency to ensure financial data access"""
    if not has_permission(role, Permission.VIEW_FINANCIAL):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Financial data access required"
        )
    return role
