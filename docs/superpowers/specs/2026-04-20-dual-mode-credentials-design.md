# 双模式凭证支持设计

## 背景

当前项目支持 CodeBuddy 官方版和企业版，但 `CODEBUDDY_API_ENDPOINT` 和 `CODEBUDDY_ENTERPRISE_ID` 是全局设置，所有凭证共享同一套配置，无法同时使用官方版和企业版凭证。

## 目标

- 支持官方版和企业版凭证同时存在、同时使用
- 每个凭证独立携带自己的 API 端点和企业 ID
- 认证流程支持选择类型
- 企业版认证信息可保存到浏览器，方便下次使用

## 设计决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 数据存储位置 | 凭证 JSON 文件内 | 数据内聚，删除凭证时自动清理 |
| 轮换策略 | 统一池轮换 | 简单，用户自己控制池中凭证组成 |
| 全局设置处理 | 完全移除 | 避免混淆，每个凭证自包含 |
| 旧凭证兼容 | 自动填充默认值 | 官方版默认值，用户可手动编辑 |
| 企业信息历史 | localStorage 列表 | 浏览器端存储，最多 10 条 |
| 手动添加 UI | 类型选择器 | 与自动认证一致的体验 |

## 数据模型变更

### 凭证 JSON 新增字段

```json
{
  "bearer_token": "eyJhbGci...",
  "user_id": "y18277",
  "api_endpoint": "https://www.codebuddy.ai",
  "enterprise_id": null,
  "created_at": 1776401199,
  "expires_in": 31535999,
  ...
}
```

- `api_endpoint`：该凭证对应的 CodeBuddy API 端点
  - 官方版：`https://www.codebuddy.ai`
  - 企业版：用户输入的地址（如 `https://h3c.copilot.qq.com`）
- `enterprise_id`：企业标识
  - 官方版：`null`
  - 企业版：如 `h3c`

### 旧凭证迁移

`load_all_tokens()` 加载凭证时，检测缺失字段并自动填充：
- `api_endpoint` 默认值：`https://www.codebuddy.ai`
- `enterprise_id` 默认值：`null`

填充后回写 JSON 文件，一次性完成迁移。

### 全局配置变更

从 `config.py` 的 `_DEFAULT_CONFIG` 中移除：
- `CODEBUDDY_API_ENDPOINT`
- `CODEBUDDY_ENTERPRISE_ID`

从设置页面移除这两个配置项的显示和编辑。

## 后端变更

### config.py

- 移除 `CODEBUDDY_API_ENDPOINT` 和 `CODEBUDDY_ENTERPRISE_ID` 的默认配置
- 删除 `get_codebuddy_api_endpoint()` 和 `get_enterprise_id()` 函数
- 所有调用方改为从凭证数据中读取

### codebuddy_token_manager.py

- `load_all_tokens()`：检测并填充缺失的 `api_endpoint`/`enterprise_id`，回写 JSON
- `get_next_credential()`：返回的凭证数据包含这两个字段

### codebuddy_auth_router.py

- `GET /codebuddy/auth/start` 新增查询参数：
  - `type`：`official` 或 `enterprise`
  - `enterprise_id`：企业标识（企业版必填）
  - `api_endpoint`：API 端点（企业版必填）
- 根据传入参数决定请求头和 API 端点
- 认证成功后将 `api_endpoint` 和 `enterprise_id` 写入凭证 JSON

### codebuddy_api_client.py

- `generate_codebuddy_headers()`：接受 `enterprise_id` 参数，不再从全局 config 读取
- API 请求的 base URL 从当前凭证的 `api_endpoint` 读取

### codebuddy_router.py

- chat completions 路由从当前凭证获取 `api_endpoint`
- `get_codebuddy_api_url()` 改为接受凭证参数

### credit_manager.py

- 积分查询从凭证数据获取 `api_endpoint` 和 `enterprise_id`

### settings_router.py

- 设置页面不再返回 `CODEBUDDY_API_ENDPOINT` 和 `CODEBUDDY_ENTERPRISE_ID`

## 前端变更

### 凭证页面 - 自动认证区域

在"开始认证"按钮上方新增类型选择器：

**官方版**：
- 自动填充 `api_endpoint = https://www.codebuddy.ai`
- `enterprise_id` 隐藏

**企业版**：
- 显示 API 端点输入框（可从 localStorage 历史列表选择）
- 显示企业 ID 输入框（可从 localStorage 历史列表选择）

**历史记录**：
- `localStorage` 键：`codebuddy_enterprise_endpoints`（JSON 数组）
- `localStorage` 键：`codebuddy_enterprise_ids`（JSON 数组）
- 输入框获得焦点时显示下拉建议列表
- 认证成功后添加到列表（去重，最多 10 条）

### 凭证列表卡片

每个凭证卡片显示类型标签：
- 官方版：标签显示"官方"
- 企业版：标签显示"企业: {enterprise_id}"

### 手动添加凭证

使用与自动认证相同的类型选择器 UI：
- 选择"官方"后自动填充端点，隐藏企业 ID
- 选择"企业"后显示端点和企业 ID 输入框

### 仪表盘

API 端点卡片调整为显示凭证类型分布（如"官方 x2, 企业 x1"），而非单一端点。

### 设置页面

移除 `CODEBUDDY_API_ENDPOINT` 和 `CODEBUDDY_ENTERPRISE_ID` 配置项。

## 影响范围

| 文件 | 变更类型 |
|------|----------|
| `config.py` | 删除全局配置和 getter 函数 |
| `src/codebuddy_token_manager.py` | 新增字段检测/填充/回写逻辑 |
| `src/codebuddy_auth_router.py` | 新增查询参数，凭证保存时写入字段 |
| `src/codebuddy_api_client.py` | 改为从凭证参数读取 endpoint/enterprise_id |
| `src/codebuddy_router.py` | 从凭证获取 endpoint |
| `src/credit_manager.py` | 从凭证获取 endpoint/enterprise_id |
| `src/settings_router.py` | 移除两个配置项 |
| `frontend/admin.html` | 认证 UI、凭证卡片、设置页面、手动添加 UI |
