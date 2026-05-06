"""
Authentication module for CodeBuddy2API
"""
from fastapi import HTTPException, Depends
from fastapi.security.utils import get_authorization_scheme_param
from starlette.requests import Request
from config import get_server_password


async def authenticate(request: Request) -> str:
    """验证用户身份"""
    password = get_server_password()
    if not password:
        # 未设置密码，跳过认证
        return "no-auth"

    # 从 Authorization 头获取 token
    authorization = request.headers.get("Authorization")
    if not authorization:
        raise HTTPException(status_code=401, detail="Not authenticated")

    scheme, param = get_authorization_scheme_param(authorization)
    if scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid authentication scheme")

    if param != password:
        raise HTTPException(status_code=403, detail="Invalid password")

    return param