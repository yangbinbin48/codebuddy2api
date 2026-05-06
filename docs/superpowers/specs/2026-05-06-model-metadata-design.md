# 模型元数据获取功能设计

## 背景

当前 `/v1/models` 端点返回的模型信息不包含上下文大小 (`context_window`)，客户端无法获取每个模型的 token 限制信息。

## 目标

实现自动从 CodeBuddy API 获取模型元数据（包括上下文大小、最大输出 tokens 等），并在 `/v1/models` 端点中返回增强的模型信息。

## 当前状态

### 现有返回格式
```json
{
  "object": "list",
  "data": [{
    "id": "glm-5.0",
    "object": "model",
    "created": 1234567890,
    "owned_by": "codebuddy"
  }]
}
```

**问题**：没有 `context_window`、`max_tokens` 等关键信息。

## API 发现

### 端点
`GET /v3/config`

### 请求头

**企业版** (`h3c.copilot.qq.com`):
```
Authorization: Bearer {token}
X-User-Id: {user_id}
X-Enterprise-Id: {enterprise_id}
X-Tenant-Id: {tenant_id}
X-Domain: {domain}
User-Agent: VSCode/1.115.0 H3CAICODE/4.2.22590715
X-Product: Cloud-Hosted
Accept: application/json, text/plain, */*
X-Requested-With: XMLHttpRequest
```

**个人版** (`copilot.tencent.com`):
```
Authorization: Bearer {token}
X-User-Id: {user_id}
X-Domain: www.codebuddy.cn
User-Agent: VSCode/1.115.0 CodeBuddy/4.3.20019762
X-Product: SaaS
Accept: application/json, text/plain, */*
X-Requested-With: XMLHttpRequest
```

### 响应格式
```json
{
  "code": 0,
  "data": {
    "models": [
      {
        "id": "glm-5.0",
        "name": "GLM-5.0",
        "maxInputTokens": 200000,
        "maxOutputTokens": 48000,
        "supportsImages": true,
        "supportsToolCall": true,
        ...
      }
    ]
  }
}
```

## 架构设计

### 新增模块
```
src/
├── model_metadata.py          # 模型元数据管理器
│   ├── ModelMetadata           # 模型元数据数据类
│   ├── ModelMetadataCache      # 缓存管理
│   └── fetch_model_config()    # 从 API 获取数据
```

### 修改模块
```
src/
├── codebuddy_router.py         # 修改 /v1/models 端点
└── models.py                   # 添加 ModelInfo 数据类
```

## 数据结构

### models.py 新增
```python
class ModelInfo(BaseModel):
    """单个模型的元数据"""
    id: str
    name: str
    max_input_tokens: int
    max_output_tokens: int
    supports_images: bool = False
    supports_tool_call: bool = True
    vendor: str = ""

class ModelWithMetadata(BaseModel):
    """增强的模型信息（用于 /v1/models 返回）"""
    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = "codebuddy"
    context_window: int      # 新增：上下文窗口大小
    max_tokens: int          # 新增：最大输出 tokens
```

## 实现方案

### 方案选择：启动时一次性获取 + 缓存

**理由**：
- 模型列表变更不频繁
- 实现简单
- 性能最好

### 初始化流程
```
服务启动
    ↓
ModelMetadataCache.initialize()
    ↓
遍历所有凭证：
    ├─ 获取 api_endpoint, user_info, site_type
    ├─ 根据 site_type 构建请求头
    ├─ GET {api_endpoint}/v3/config
    └─ 解析并合并 models 数据
    ↓
存储到全局缓存 _model_cache
```

### 请求头构建逻辑
```python
def build_config_headers(credential: dict) -> dict:
    """根据凭证类型构建请求头"""
    headers = {
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    }

    if credential.get("site_type") == "enterprise":
        # 企业版请求头
        headers.update({
            "X-User-Id": credential["user_info"]["sub"],
            "X-Enterprise-Id": credential.get("enterprise_id"),
            "X-Tenant-Id": credential.get("enterprise_id"),
            "X-Domain": credential.get("domain"),
            "X-Product": "Cloud-Hosted",
            "User-Agent": credential.get("user_agent", "VSCode/1.115.0 H3CAICODE/4.2.22590715"),
        })
    else:
        # 个人版请求头
        headers.update({
            "X-User-Id": credential["user_info"]["sub"],
            "X-Domain": credential.get("domain", "www.codebuddy.cn"),
            "X-Product": "SaaS",
            "User-Agent": credential.get("user_agent", "VSCode/1.115.0 CodeBuddy/4.3.20019762"),
        })

    return headers
```

## 错误处理与降级策略

### 1. 获取失败降级
```
尝试获取 /v3/config
    ↓
失败（网络/认证/返回null）
    ↓
降级策略：
  - 使用配置中的模型列表
  - 每个模型使用默认 context_window (200000)
  - 记录警告日志
```

### 2. 部分凭证处理
```
多凭证场景：
  - 凭证 A 成功 → 使用 A 的数据
  - 凭证 B 失败 → 记录警告，继续使用 A 的数据
  - 全部失败 → 使用配置默认值
```

### 3. /v1/models 端点降级
```python
async def list_v1_models(...):
    models = get_available_models()

    if model_cache.is_empty():
        # 缓存为空，返回基础数据 + 默认值
        return [{
            "id": m,
            "object": "model",
            "created": int(time.time()),
            "owned_by": "codebuddy",
            "context_window": get_default_context_window(),
            "max_tokens": get_default_max_tokens(),
        } for m in models]

    # 使用缓存数据
    return model_cache.get_enhanced_model_list(models)
```

## 配置新增

```python
# config.py
_DEFAULT_CONFIG = {
    ...
    "CODEBUDDY_DEFAULT_CONTEXT_WINDOW": 200000,
    "CODEBUDDY_DEFAULT_MAX_TOKENS": 4096,
}
```

## 测试策略

### 单元测试
- `test_fetch_config_success()` - Mock 成功响应
- `test_fetch_config_failure()` - Mock 失败响应
- `test_enterprise_headers()` - 验证企业版请求头
- `test_personal_headers()` - 验证个人版请求头
- `test_model_cache_fallback()` - 验证降级逻辑

### 集成测试
- `test_models_endpoint()` - 验证 /v1/models 返回增强数据
- `test_partial_failure()` - 验证部分凭证失败场景

### 手动验证
```bash
# 1. 启动服务，查看日志
python -m src.web

# 2. 调用端点
curl http://localhost:8010/v1/models

# 3. 验证返回数据包含 context_window
```

## 实施步骤

1. [ ] 创建 `src/model_metadata.py`
2. [ ] 修改 `src/models.py` 添加数据类
3. [ ] 修改 `src/codebuddy_router.py` 更新 /v1/models
4. [ ] 修改 `config.py` 添加默认值
5. [ ] 编写单元测试
6. [ ] 手动验证功能

## 主要模型上下文大小参考

| 模型 | maxInputTokens | maxOutputTokens |
|------|----------------|-----------------|
| glm-5.0 | 200,000 | 48,000 |
| glm-5.1 | 200,000 | 48,000 |
| glm-4.7 | 200,000 | 48,000 |
| glm-4.6 | 168,000 | 32,000 |
| kimi-k2.6 | 256,000 | 32,000 |
| deepseek-v4-flash | 1,000,000 | 50,000 |
| hy3-preview | 192,000 | 64,000 |
