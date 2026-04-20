"""
Anthropic Messages API Router

提供 Anthropic Messages API 兼容端点，供 Claude Code 等工具直接连接。
请求/响应格式遵循 Anthropic Messages API 规范。
"""
import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import StreamingResponse

from .anthropic_auth import authenticate_anthropic
from .anthropic_converter import convert_request, convert_response, AnthropicStreamConverter
from .codebuddy_router import (
    get_http_client, get_codebuddy_api_url, parse_sse_line,
    RequestProcessor, CredentialManager, StreamResponseAggregator,
    SSE_HEADERS, get_available_models_list,
)
from .codebuddy_api_client import codebuddy_api_client
from .usage_stats_manager import usage_stats_manager

logger = logging.getLogger(__name__)


def _resolve_upstream_model(requested_model: str) -> str:
    """将 Anthropic 请求中的模型名映射为 CodeBuddy 支持的模型名。

    如果请求的模型在可用列表中，直接使用；
    否则使用可用模型列表中的第一个作为默认值。
    """
    available = get_available_models_list()
    if requested_model in available:
        return requested_model
    # Claude Code 等工具会发送 claude-xxx 模型名，需要替换
    default = available[0] if available else "auto-chat"
    logger.info(f"Model mapping: {requested_model} -> {default}")
    return default

router = APIRouter()


def _anthropic_error(status_code: int, error_type: str, message: str):
    """构造 Anthropic 格式的错误响应"""
    raise HTTPException(
        status_code=status_code,
        detail={
            "type": "error",
            "error": {"type": error_type, "message": message}
        }
    )


@router.post("/v1/messages")
async def messages(
    request: Request,
    _token: str = Depends(authenticate_anthropic),
):
    """Anthropic Messages API 端点"""
    try:
        # 解析请求体
        try:
            request_body = await request.json()
        except Exception as e:
            _anthropic_error(400, "invalid_request_error", f"Invalid JSON: {e}")

        # 转换为 OpenAI 格式
        openai_request = convert_request(request_body)

        # 将 Claude 模型名映射为 CodeBuddy 支持的模型名
        requested_model = request_body.get("model", "unknown")
        upstream_model = _resolve_upstream_model(requested_model)
        openai_request["model"] = upstream_model

        # 获取凭证
        credential = CredentialManager.get_valid_credential()

        # 生成请求头
        headers = codebuddy_api_client.generate_codebuddy_headers(
            bearer_token=credential.get('bearer_token'),
            user_id=credential.get('user_id'),
        )

        # 预处理载荷 (设置 stream=True, 确保 2+ 消息, 关键词替换)
        payload = RequestProcessor.prepare_payload(openai_request)

        # 用请求中的模型名作为响应中的 model 字段（让 Claude Code 认为自己在用原模型）
        model = requested_model
        usage_stats_manager.record_model_usage(upstream_model)

        wants_stream = request_body.get("stream", False)
        # 预估 input_tokens（Claude Code 用此显示上下文占用）
        estimated_input_tokens = _estimate_input_tokens(payload)

        if wants_stream:
            return await _handle_stream(payload, headers, model, estimated_input_tokens)
        else:
            return await _handle_non_stream(payload, headers, model, estimated_input_tokens)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Anthropic Messages API error: {e}")
        _anthropic_error(500, "api_error", str(e))


async def _handle_stream(
    payload: dict,
    headers: dict,
    model: str,
    estimated_input_tokens: int = 0,
) -> StreamingResponse:
    """处理流式请求: CodeBuddy SSE -> Anthropic SSE"""

    async def stream_generator():
        converter = AnthropicStreamConverter(model, estimated_input_tokens)
        client = await get_http_client()

        try:
            async with client.stream(
                "POST", get_codebuddy_api_url(),
                json=payload, headers=headers,
            ) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    error_msg = error_text.decode('utf-8', errors='ignore')
                    logger.error(f"CodeBuddy API error (stream): {response.status_code} - {error_msg}")
                    error_data = {
                        "type": "error",
                        "error": {
                            "type": "api_error",
                            "message": f"Upstream error ({response.status_code}): {error_msg}"
                        }
                    }
                    yield f"event: error\ndata: {json.dumps(error_data, ensure_ascii=False)}\n\n"
                    return

                buffer = ""
                async for chunk in response.aiter_text(chunk_size=8192):
                    if not chunk:
                        continue
                    buffer += chunk

                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        if not line.strip() or line.startswith(':'):
                            continue
                        if '[DONE]' in line:
                            events = converter.process_done()
                            if events:
                                yield events
                            return

                        chunk_data = parse_sse_line(line)
                        if chunk_data:
                            events = converter.process_chunk(chunk_data)
                            if events:
                                yield events

                # 处理缓冲区剩余数据
                if buffer.strip():
                    if '[DONE]' in buffer:
                        events = converter.process_done()
                    else:
                        chunk_data = parse_sse_line(buffer.strip())
                        if chunk_data:
                            events = converter.process_chunk(chunk_data)
                            if events:
                                yield events
                            done_events = converter.process_done()
                            if done_events:
                                yield done_events
                    return

                # 如果流正常结束但没有收到 [DONE]，也确保关闭
                done_events = converter.process_done()
                if done_events:
                    yield done_events

        except Exception as e:
            logger.error(f"Stream error: {e}")
            if not converter.finished:
                error_data = {
                    "type": "error",
                    "error": {"type": "api_error", "message": str(e)}
                }
                yield f"event: error\ndata: {json.dumps(error_data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


async def _handle_non_stream(
    payload: dict,
    headers: dict,
    model: str,
    estimated_input_tokens: int = 0,
) -> dict:
    """处理非流式请求: 聚合 CodeBuddy SSE -> Anthropic Messages 响应"""
    client = await get_http_client()
    response = await client.post(
        get_codebuddy_api_url(),
        json=payload, headers=headers,
    )

    if response.status_code != 200:
        error_msg = response.text
        logger.error(f"CodeBuddy API error (non-stream): {response.status_code} - {error_msg}")
        _anthropic_error(502, "api_error", f"Upstream error ({response.status_code}): {error_msg}")

    # 聚合 SSE 流
    aggregator = StreamResponseAggregator()
    buffer = ""

    async for chunk in response.aiter_text():
        if not chunk:
            continue
        buffer += chunk
        while '\n' in buffer:
            line, buffer = buffer.split('\n', 1)
            obj = parse_sse_line(line)
            if obj:
                aggregator.process_chunk(obj)

    if buffer.strip():
        obj = parse_sse_line(buffer.strip())
        if obj:
            aggregator.process_chunk(obj)

    openai_response = aggregator.finalize()
    return convert_response(openai_response, model)


@router.post("/v1/messages/count_tokens")
async def count_tokens(
    request: Request,
    _token: str = Depends(authenticate_anthropic),
):
    """Anthropic count_tokens 端点 — 估算输入 token 数量

    Claude Code 会在发送消息前调用此接口估算 token 用量。
    优先使用 tiktoken 精确计数，不可用时回退到字符估算。
    """
    try:
        try:
            request_body = await request.json()
        except Exception as e:
            _anthropic_error(400, "invalid_request_error", f"Invalid JSON: {e}")

        # 尝试使用 tiktoken 精确计数
        token_count = _count_tokens_tiktoken(request_body)

        return {
            "id": f"msg_{uuid.uuid4().hex[:24]}",
            "type": "count_tokens",
            "input_tokens": token_count,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"count_tokens error: {e}")
        _anthropic_error(500, "api_error", str(e))


def _count_tokens_tiktoken(request_body: dict) -> int:
    """使用 tiktoken 精确计数，回退到字符估算"""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        use_tiktoken = True
    except (ImportError, Exception):
        use_tiktoken = False

    total_chars = 0
    text_parts: list = []

    # system
    system = request_body.get("system")
    if system:
        if isinstance(system, str):
            text_parts.append(system)
            total_chars += len(system)
        elif isinstance(system, list):
            for block in system:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                    total_chars += len(block.get("text", ""))
                elif isinstance(block, str):
                    text_parts.append(block)
                    total_chars += len(block)

    # messages
    for msg in request_body.get("messages", []):
        content = msg.get("content", "")
        if isinstance(content, str):
            text_parts.append(content)
            total_chars += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type")
                    if block_type == "text":
                        text_parts.append(block.get("text", ""))
                        total_chars += len(block.get("text", ""))
                    elif block_type == "tool_result":
                        rc = block.get("content", "")
                        if isinstance(rc, str):
                            text_parts.append(rc)
                            total_chars += len(rc)
                        elif isinstance(rc, list):
                            for item in rc:
                                if isinstance(item, dict) and item.get("type") == "text":
                                    text_parts.append(item.get("text", ""))
                                    total_chars += len(item.get("text", ""))
                    elif block_type == "tool_use":
                        args_json = json.dumps(block.get("input", {}), ensure_ascii=False)
                        text_parts.append(args_json)
                        text_parts.append(block.get("name", ""))
                        total_chars += len(args_json) + len(block.get("name", ""))

    # tools 定义
    for tool in request_body.get("tools", []):
        tool_json = json.dumps(tool, ensure_ascii=False)
        text_parts.append(tool_json)
        total_chars += len(tool_json)

    if use_tiktoken:
        all_text = "\n".join(text_parts)
        return len(enc.encode(all_text))
    else:
        # 回退: 保守估算 ~2 字符/token + 缓冲
        return int(total_chars / 2.0) + 50


def _estimate_input_tokens(payload: dict) -> int:
    """从 OpenAI 格式 payload 中预估 input token 数"""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        use_tiktoken = True
    except (ImportError, Exception):
        use_tiktoken = False

    text_parts = []
    for msg in payload.get("messages", []):
        content = msg.get("content", "")
        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))

    # tools
    for tool in payload.get("tools", []):
        text_parts.append(json.dumps(tool, ensure_ascii=False))

    if use_tiktoken:
        all_text = "\n".join(text_parts)
        return len(enc.encode(all_text))
    else:
        total_chars = sum(len(t) for t in text_parts)
        return int(total_chars / 2.0) + 50
