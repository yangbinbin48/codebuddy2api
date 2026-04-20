"""
Anthropic API Authentication Module
"""
from typing import Optional
from fastapi import HTTPException, Header
from config import get_server_password


def authenticate_anthropic(
    x_api_key: Optional[str] = Header(None, alias="x-api-key"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> str:
    """验证 Anthropic API 请求

    支持两种认证方式:
    1. x-api-key 头 (Anthropic 标准)
    2. Authorization: Bearer xxx 头 (Claude Code ANTHROPIC_AUTH_TOKEN)
    """
    password = get_server_password()
    if not password:
        raise HTTPException(
            status_code=500,
            detail={
                "type": "error",
                "error": {
                    "type": "api_error",
                    "message": "CODEBUDDY_PASSWORD is not configured on the server."
                }
            }
        )

    # 提取 token: 优先 x-api-key, 其次 Authorization: Bearer xxx
    token = None
    if x_api_key:
        token = x_api_key
    elif authorization:
        if authorization.startswith("Bearer "):
            token = authorization[7:]
        elif authorization.startswith("bearer "):
            token = authorization[7:]
        else:
            token = authorization

    if not token:
        raise HTTPException(
            status_code=401,
            detail={
                "type": "error",
                "error": {
                    "type": "authentication_error",
                    "message": "x-api-key or Authorization header is required."
                }
            }
        )

    if token != password:
        raise HTTPException(
            status_code=401,
            detail={
                "type": "error",
                "error": {
                    "type": "authentication_error",
                    "message": "invalid x-api-key."
                }
            }
        )

    return token
