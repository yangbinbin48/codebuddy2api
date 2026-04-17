"""
Anthropic API Authentication Module
"""
from typing import Optional
from fastapi import HTTPException, Header
from config import get_server_password


def authenticate_anthropic(
    x_api_key: Optional[str] = Header(None, alias="x-api-key")
) -> str:
    """验证 Anthropic API key (x-api-key header)"""
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

    if not x_api_key:
        raise HTTPException(
            status_code=401,
            detail={
                "type": "error",
                "error": {
                    "type": "authentication_error",
                    "message": "x-api-key header is required."
                }
            }
        )

    if x_api_key != password:
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

    return x_api_key
