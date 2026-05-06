# Anthropic Messages API 兼容层设计

## 目标

在现有 OpenAI 兼容 API 之外，新增 Anthropic Messages API 兼容端点，使 Claude Code 能够直接配置连接到本服务。

## 端点

| 端点 | 方法 | 功能 |
|------|------|------|
| `/anthropic/v1/messages` | POST | Anthropic Messages API |
| `/anthropic/v1/messages` | OPTIONS | CORS preflight |

## 架构

```
web.py
├── /codebuddy/*      → codebuddy_router.py    (现有，OpenAI 兼容)
└── /anthropic/*      → anthropic_router.py    (新增)
                         ├── anthropic_auth.py       (x-api-key 认证)
                         └── anthropic_converter.py  (格式双向转换)
```

新增 3 个文件，修改 `web.py` 注册新路由。共用现有凭证管理、HTTP 客户端池、关键词替换。

## 认证

- 使用 `x-api-key` header，密码复用 `CODEBUDDY_PASSWORD`
- 接受 `anthropic-version` header，不强制校验版本

## 格式转换 (anthropic_converter.py)

### 请求方向: Anthropic → OpenAI/CodeBuddy

| Anthropic 字段 | OpenAI 字段 |
|----------------|-------------|
| `system` (独立字段) | `messages[0]` with `role=system` |
| `tools[].input_schema` | `tools[].function.parameters` |
| `tool_choice` (auto/any/tool) | `tool_choice` (auto/required/function) |
| `tool_result` content block | `role=tool` message + `tool_call_id` |
| `tool_use` in assistant content | `role=assistant` + `tool_calls[]` |
| `max_tokens` | `max_tokens` |
| `messages[].content` (blocks array) | `messages[].content` (string or array) |

### 响应方向: CodeBuddy SSE → Anthropic SSE

流式事件映射：

| CodeBuddy/OpenAI 事件 | Anthropic SSE 事件 |
|----------------------|-------------------|
| 请求开始 | `event: message_start` + `event: content_block_start(index=0, type=text)` |
| `choices[0].delta.content` | `event: content_block_delta(index=0, text_delta)` |
| `choices[0].delta.tool_calls` | `event: content_block_start(type=tool_use)` + `event: content_block_delta(tool_use_delta)` |
| thinking 内容 | `event: content_block_start(type=thinking)` + `event: content_block_delta(thinking_delta)` |
| 流结束 | `event: content_block_stop` + `event: message_delta(stop_reason)` + `event: message_stop` |

非流式响应：聚合 SSE 后构建 Anthropic Messages 响应格式：
```json
{
  "id": "msg_xxx",
  "type": "message",
  "role": "assistant",
  "content": [{"type": "text", "text": "..."}],
  "model": "xxx",
  "stop_reason": "end_turn",
  "usage": {"input_tokens": N, "output_tokens": N}
}
```

### Thinking Blocks 处理

检测模型输出中的 thinking 内容（`<antThinking>` 标签或特定标记），将其包装为 Anthropic `thinking` content block。流式场景下需要缓冲检测开始标签，然后分别输出 thinking_delta 和 text_delta。

## 共用组件

- `codebuddy_token_manager` — 凭证获取与轮换
- `codebuddy_api_client.generate_codebuddy_headers()` — HTTP 头生成
- `get_http_client()` — 异步 HTTP 连接池
- `keyword_replacer` — 系统消息关键词替换

## 不在范围内

- 不修改现有 OpenAI 兼容路由
- 不修改前端管理界面（本次不涉及）
- 不新增模型映射（模型名称直接透传）
