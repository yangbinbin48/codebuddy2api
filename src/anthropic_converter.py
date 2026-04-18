"""
Anthropic Messages API <-> OpenAI Chat Completion API 格式转换器

负责两个方向的格式转换：
1. 请求方向: Anthropic Messages -> OpenAI Chat Completion
2. 响应方向: OpenAI Chat Completion -> Anthropic Messages
"""
import json
import uuid
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


# =====================================================================
#  请求方向: Anthropic -> OpenAI
# =====================================================================

def convert_request(anthropic_req: Dict[str, Any]) -> Dict[str, Any]:
    """将 Anthropic Messages API 请求转换为 OpenAI Chat Completion 格式"""
    openai_req: Dict[str, Any] = {"stream": True}  # CodeBuddy 只支持流式

    # 基础字段直接映射
    openai_req["model"] = anthropic_req.get("model", "")
    if "max_tokens" in anthropic_req:
        openai_req["max_tokens"] = anthropic_req["max_tokens"]
    if "temperature" in anthropic_req:
        openai_req["temperature"] = anthropic_req["temperature"]
    if "top_p" in anthropic_req:
        openai_req["top_p"] = anthropic_req["top_p"]
    if "stop_sequences" in anthropic_req:
        openai_req["stop"] = anthropic_req["stop_sequences"]

    # 构建消息列表
    messages: List[Dict[str, Any]] = []

    # system -> system message
    system = anthropic_req.get("system")
    if system:
        messages.append({"role": "system", "content": _extract_system_text(system)})

    # 转换 messages
    for msg in anthropic_req.get("messages", []):
        role = msg["role"]
        content = msg.get("content", "")
        if role == "user":
            messages.extend(_convert_user_content(content))
        elif role == "assistant":
            messages.append(_convert_assistant_content(content))

    openai_req["messages"] = messages

    # tools
    if "tools" in anthropic_req:
        openai_req["tools"] = _convert_tools(anthropic_req["tools"])

    # tool_choice
    if "tool_choice" in anthropic_req:
        openai_req["tool_choice"] = _convert_tool_choice(anthropic_req["tool_choice"])

    return openai_req


def _extract_system_text(system) -> str:
    """从 Anthropic system 字段提取文本"""
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts = []
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(system)


def _convert_user_content(content) -> List[Dict[str, Any]]:
    """转换 Anthropic user 消息 -> OpenAI 消息列表

    Anthropic user 消息中可能包含 tool_result content blocks，
    需要拆分为独立的 tool messages。
    """
    if isinstance(content, str):
        return [{"role": "user", "content": content}]

    if not isinstance(content, list):
        return [{"role": "user", "content": str(content)}]

    messages: List[Dict[str, Any]] = []
    text_parts: List[str] = []

    for block in content:
        if not isinstance(block, dict):
            text_parts.append(str(block))
            continue

        block_type = block.get("type")
        if block_type == "text":
            text_parts.append(block.get("text", ""))
        elif block_type == "tool_result":
            tool_use_id = block.get("tool_use_id", "")
            result_content = block.get("content", "")
            # content 可能是 string 或 content blocks array
            if isinstance(result_content, list):
                result_texts = []
                for item in result_content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        result_texts.append(item.get("text", ""))
                    elif isinstance(item, str):
                        result_texts.append(item)
                result_content = "\n".join(result_texts)
            # toolu_xxx -> call_xxx (OpenAI format)
            openai_tool_id = _anthropic_to_openai_tool_id(tool_use_id)
            messages.append({
                "role": "tool",
                "tool_call_id": openai_tool_id,
                "content": str(result_content)
            })
        # image blocks 等暂时忽略

    if text_parts:
        messages.insert(0, {"role": "user", "content": "\n".join(text_parts)})

    if not messages:
        messages.append({"role": "user", "content": ""})

    return messages


def _convert_assistant_content(content) -> Dict[str, Any]:
    """转换 Anthropic assistant 消息 -> OpenAI assistant message"""
    if isinstance(content, str):
        return {"role": "assistant", "content": content}

    if not isinstance(content, list):
        return {"role": "assistant", "content": str(content)}

    text_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []

    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text_parts.append(block.get("text", ""))
        elif block_type == "tool_use":
            tool_id = block.get("id", "")
            openai_tool_id = _anthropic_to_openai_tool_id(tool_id)
            try:
                arguments = json.dumps(block.get("input", {}), ensure_ascii=False)
            except (TypeError, ValueError):
                arguments = "{}"
            tool_calls.append({
                "id": openai_tool_id,
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": arguments
                }
            })
        elif block_type == "thinking":
            # thinking blocks 暂时跳过，不转换为 OpenAI 格式
            pass

    message: Dict[str, Any] = {
        "role": "assistant",
        "content": "\n".join(text_parts) if text_parts else ""
    }
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def _convert_tools(anthropic_tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """转换 Anthropic tools (input_schema) -> OpenAI tools (function)"""
    openai_tools = []
    for tool in anthropic_tools:
        openai_tools.append({
            "type": "function",
            "function": {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {})
            }
        })
    return openai_tools


def _convert_tool_choice(tool_choice) -> Any:
    """转换 Anthropic tool_choice -> OpenAI tool_choice"""
    if isinstance(tool_choice, str):
        mapping = {"auto": "auto", "any": "required", "none": "none"}
        return mapping.get(tool_choice, "auto")
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "tool":
        return {
            "type": "function",
            "function": {"name": tool_choice.get("name", "")}
        }
    return "auto"


# =====================================================================
#  Tool ID 格式转换
# =====================================================================

def _anthropic_to_openai_tool_id(tool_id: str) -> str:
    """toolu_xxx -> call_xxx"""
    if tool_id.startswith("toolu_"):
        return "call_" + tool_id[6:]
    return tool_id


def _to_anthropic_tool_id(tool_id: str) -> str:
    """call_xxx / tooluse_xxx -> toolu_xxx"""
    if tool_id.startswith("call_"):
        return "toolu_" + tool_id[5:]
    if tool_id.startswith("tooluse_"):
        return "toolu_" + tool_id[8:]
    return tool_id


# =====================================================================
#  响应方向: OpenAI -> Anthropic (非流式)
# =====================================================================

def convert_response(openai_response: Dict[str, Any], model: str) -> Dict[str, Any]:
    """将 OpenAI Chat Completion 响应转换为 Anthropic Messages 格式"""
    choice = openai_response.get("choices", [{}])[0]
    message = choice.get("message", {})

    content: List[Dict[str, Any]] = []

    # 文本内容
    text = message.get("content")
    if text:
        content.append({"type": "text", "text": text})

    # 工具调用
    tool_calls = message.get("tool_calls", [])
    for tc in tool_calls:
        tool_id = _to_anthropic_tool_id(tc.get("id", ""))
        try:
            input_data = json.loads(tc.get("function", {}).get("arguments", "{}"))
        except (json.JSONDecodeError, TypeError):
            input_data = {}
        content.append({
            "type": "tool_use",
            "id": tool_id,
            "name": tc.get("function", {}).get("name", ""),
            "input": input_data
        })

    if not content:
        content.append({"type": "text", "text": ""})

    # stop_reason
    finish_reason = choice.get("finish_reason", "stop")
    stop_reason = _convert_stop_reason(finish_reason, bool(tool_calls))

    # usage
    usage = openai_response.get("usage", {})

    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": model,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
    }


def _convert_stop_reason(finish_reason: str, has_tool_calls: bool) -> str:
    """OpenAI finish_reason -> Anthropic stop_reason"""
    if has_tool_calls or finish_reason == "tool_calls":
        return "tool_use"
    if finish_reason == "length":
        return "max_tokens"
    return "end_turn"


# =====================================================================
#  响应方向: OpenAI SSE -> Anthropic SSE (流式)
# =====================================================================

class AnthropicStreamConverter:
    """有状态的流式转换器: OpenAI SSE chunks -> Anthropic SSE events

    跟踪 content block 索引、类型切换，生成正确的 Anthropic 流式事件序列。
    参照 1rgs/claude-code-proxy 实现，支持 ping 事件、[DONE] 标记、
    以及 text/tool block 切换的边界处理。
    """

    def __init__(self, model: str):
        self.message_id = f"msg_{uuid.uuid4().hex[:24]}"
        self.model = model
        self.content_block_index = -1
        self.current_block_type: Optional[str] = None  # 'text' | 'tool_use'
        self.message_started = False
        self.finished = False
        self.had_tool_calls = False
        # CodeBuddy 的 tool call index -> 当前 block 映射
        self._tool_block_map: Dict[int, int] = {}
        # 追踪是否已发送过文本内容（用于 text->tool 切换判断）
        self._text_sent = False
        # 追踪 text block 是否已关闭
        self._text_block_closed = False
        # 累积文本（用于延迟发送场景）
        self._accumulated_text = ""

    # --- SSE 格式化 ---

    @staticmethod
    def _sse(event: str, data: Dict[str, Any]) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    # --- 事件生成 ---

    def _emit_message_start(self) -> str:
        return self._sse("message_start", {
            "type": "message_start",
            "message": {
                "id": self.message_id,
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": self.model,
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 0,
                }
            }
        })

    def _emit_ping(self) -> str:
        """发送 ping 事件保持连接活跃（Anthropic 标准行为）"""
        return self._sse("ping", {"type": "ping"})

    def _start_text_block(self) -> str:
        self.content_block_index += 1
        self.current_block_type = "text"
        return self._sse("content_block_start", {
            "type": "content_block_start",
            "index": self.content_block_index,
            "content_block": {"type": "text", "text": ""}
        })

    def _start_tool_block(self, tool_id: str, tool_name: str) -> str:
        self.content_block_index += 1
        self.current_block_type = "tool_use"
        self.had_tool_calls = True
        anthropic_id = _to_anthropic_tool_id(tool_id)
        return self._sse("content_block_start", {
            "type": "content_block_start",
            "index": self.content_block_index,
            "content_block": {
                "type": "tool_use",
                "id": anthropic_id,
                "name": tool_name,
                "input": {}
            }
        })

    def _text_delta(self, text: str) -> str:
        return self._sse("content_block_delta", {
            "type": "content_block_delta",
            "index": self.content_block_index,
            "delta": {"type": "text_delta", "text": text}
        })

    def _tool_delta(self, partial_json: str) -> str:
        return self._sse("content_block_delta", {
            "type": "content_block_delta",
            "index": self.content_block_index,
            "delta": {"type": "input_json_delta", "partial_json": partial_json}
        })

    def _stop_block(self) -> str:
        event = self._sse("content_block_stop", {
            "type": "content_block_stop",
            "index": self.content_block_index
        })
        self.current_block_type = None
        return event

    def _close_text_if_open(self) -> str:
        """关闭 text block（如果它还是打开状态）"""
        events = ""
        if self.current_block_type == "text":
            if self._accumulated_text and not self._text_sent:
                events += self._text_delta(self._accumulated_text)
                self._text_sent = True
            events += self._stop_block()
            self._text_block_closed = True
        return events

    def _close_and_finish(self, stop_reason: str) -> str:
        """关闭当前 block 并发送 message_delta + message_stop + [DONE]"""
        events = ""
        if self.current_block_type:
            # 如果有累积文本但还没发送，先发出去
            if (self.current_block_type == "text"
                    and self._accumulated_text and not self._text_sent):
                events += self._text_delta(self._accumulated_text)
                self._text_sent = True
            events += self._stop_block()
        events += self._sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": 0}
        })
        events += self._sse("message_stop", {"type": "message_stop"})
        events += "data: [DONE]\n\n"
        self.finished = True
        return events

    # --- 核心处理 ---

    def process_chunk(self, chunk_data: Dict[str, Any]) -> str:
        """处理一个 OpenAI SSE chunk，返回 Anthropic SSE 事件字符串"""
        if not chunk_data.get('choices'):
            return ""

        events = ""
        choice = chunk_data['choices'][0]
        delta = choice.get('delta', {})
        finish_reason = choice.get('finish_reason')

        # 首个 chunk: 发送 message_start + content_block_start(text) + ping
        if not self.message_started:
            events += self._emit_message_start()
            events += self._start_text_block()
            events += self._emit_ping()
            self.message_started = True

        # 处理 finish_reason
        if finish_reason:
            stop_reason = "tool_use" if self.had_tool_calls else _convert_stop_reason(finish_reason, False)
            events += self._close_and_finish(stop_reason)
            return events

        # 处理文本内容
        text = delta.get('content')
        if text is not None and text != "":
            self._accumulated_text += text
            # 仅在没有 tool call 进行时立即发送 text delta
            if not self.had_tool_calls and not self._text_block_closed:
                events += self._text_delta(text)
                self._text_sent = True

        # 处理工具调用
        tool_calls = delta.get('tool_calls')
        if tool_calls:
            events += self._process_tool_calls(tool_calls)

        return events

    def _process_tool_calls(self, tool_calls: List[Dict[str, Any]]) -> str:
        """处理 OpenAI tool_calls delta"""
        events = ""

        for tc in tool_calls:
            tc_index = tc.get('index', 0)
            has_id = bool(tc.get('id'))
            has_name = bool(tc.get('function', {}).get('name'))

            if has_id or has_name:
                # 新的 tool call 开始 — 先关闭可能打开的 text block
                if self.current_block_type == "text" and not self._text_block_closed:
                    # 如果累积了文本但还没发送，先发送
                    if self._accumulated_text and not self._text_sent:
                        events += self._text_delta(self._accumulated_text)
                        self._text_sent = True
                    events += self._stop_block()
                    self._text_block_closed = True
                elif self.current_block_type is not None:
                    events += self._stop_block()

                tool_id = tc.get('id', f"call_{uuid.uuid4().hex[:24]}")
                tool_name = tc.get('function', {}).get('name', "")

                events += self._start_tool_block(tool_id, tool_name)
                self._tool_block_map[tc_index] = self.content_block_index

                # 可能同时带有初始 arguments
                args = tc.get('function', {}).get('arguments', '')
                if args:
                    events += self._tool_delta(args)
            else:
                # 已有 tool call 的增量 arguments
                args = tc.get('function', {}).get('arguments', '')
                if args and self.current_block_type == "tool_use":
                    events += self._tool_delta(args)

        return events

    def process_done(self) -> str:
        """处理 [DONE] 信号，确保流正确结束"""
        if self.finished:
            return ""
        stop_reason = "tool_use" if self.had_tool_calls else "end_turn"
        return self._close_and_finish(stop_reason)
