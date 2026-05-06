# 积分感知的多账号凭证管理设计

## 背景

当前项目支持多账号轮询，但缺乏积分感知能力：
- 无法查询每个账号的剩余积分
- 积分耗尽的账号不会自动屏蔽
- 请求失败后不会自动切换到下一个账号
- 前端不显示积分信息

## 目标

1. 查询并展示每个凭证的积分（剩余/总共/已用）
2. 积分耗尽的凭证自动屏蔽，不参与轮询
3. 请求因积分不足失败时，自动重试下一个凭证
4. 所有凭证积分耗尽时，返回明确的错误提示

## 积分查询 API

CodeBuddy 提供计费查询接口：

- **URL**: `POST {CODEBUDDY_API_ENDPOINT}/billing/meter/get-user-resource`
- **认证**: `Authorization: Bearer {token}`（与 chat API 相同的 bearer token）
- **请求体**:
  ```json
  {
    "PageNumber": 1,
    "PageSize": 200,
    "ProductCode": "p_tcaca",
    "Status": [0, 3],
    "PackageEndTimeRangeBegin": "<当前日期>",
    "PackageEndTimeRangeEnd": "<当前日期+100年>"
  }
  ```
- **响应结构**:
  ```json
  {
    "code": 0,
    "msg": "OK",
    "data": {
      "Response": {
        "Data": {
          "TotalCount": 3,
          "TotalDosage": 4500,
          "Accounts": [
            {
              "PackageName": "CodeBuddy个人体验版",
              "CapacityRemain": 500,
              "CapacityUsed": 0,
              "CapacitySize": 500,
              "CycleCapacityRemain": 500,
              "CycleCapacitySize": 500,
              "Status": 0,
              "CycleStartTime": "2026-04-01 00:00:00",
              "CycleEndTime": "2026-04-30 23:59:59"
            }
          ]
        }
      }
    }
  }
  ```

**关键字段**:
- `Status`: 0 = 活跃，3 = 已过期
- `CapacityRemain`: 剩余积分
- `CapacitySize`: 总积分
- `CapacityUsed`: 已用积分
- `TotalDosage`: 所有活跃包的剩余积分总和

**域名规则**: URL 使用 `CODEBUDDY_API_ENDPOINT` 拼接，企业版和 SaaS 版自动适配。

## 模块设计

### 模块 1: CreditManager (`src/credit_manager.py`)

新建文件，职责：查询、缓存和管理每个凭证的积分数据。

**类**: `CreditManager`

**数据结构**:
```python
# 每个凭证的积分快照
{
    "index": 0,
    "filename": "codebuddy_xxx.json",
    "total_credits": 4500,      # 所有活跃包的 CapacitySize 之和
    "remain_credits": 3500,      # 所有活跃包的 CapacityRemain 之和
    "used_credits": 1000,        # 所有活跃包的 CapacityUsed 之和
    "packages": [                # 每个包的明细
        {
            "name": "CodeBuddy个人体验版",
            "remain": 500,
            "total": 500,
            "used": 0,
            "status": 0,
            "cycle_end": "2026-04-30 23:59:59"
        }
    ],
    "last_updated": 1745000000,
    "is_depleted": False
}
```

**核心方法**:
- `async query_credential_credits(credential_data, index)`: 查询单个凭证的积分
- `async refresh_all_credits()`: 刷新所有凭证的积分
- `get_credits_info(index)`: 获取指定凭证的缓存积分信息
- `get_all_credits_summary()`: 获取所有凭证的积分汇总
- `mark_depleted(index)`: 标记凭证为积分耗尽
- `mark_available(index)`: 标记凭证为可用
- `is_depleted(index)`: 检查凭证是否积分耗尽

**缓存策略**:
- 积分数据缓存在内存中
- 缓存有效期：10 分钟
- 每次查询前检查缓存，未过期则直接返回
- 支持手动触发刷新

**后台刷新**:
- 应用启动时查询一次
- 之后每 10 分钟自动刷新（通过 `asyncio.create_task` 在 FastAPI lifespan 中启动）
- 查询失败不影响服务运行，使用上一次缓存数据

### 模块 2: 智能凭证调度（修改 `src/codebuddy_token_manager.py`）

在现有 `get_next_credential()` 中集成积分感知：

**修改点**:
1. `get_next_credential()` 增加积分过滤逻辑：
   - 现有逻辑：跳过过期凭证
   - 新增逻辑：跳过 `CreditManager.is_depleted(index)` 为 True 的凭证
2. 在过滤无效凭证的循环中，同时检查过期和积分耗尽

**不新增方法**，保持与现有轮询逻辑的兼容。`CreditManager` 作为外部依赖注入判断条件。

### 模块 3: 请求失败自动降级（修改 `src/codebuddy_router.py` 和 `src/anthropic_router.py`）

**修改点**:

1. `chat_completions()` 和 `messages()` 路由中增加重试逻辑：
   - 当 CodeBuddy API 返回非 200 且错误信息包含积分相关关键词时（如"insufficient"、"quota"、"credits"、"配额"、"积分"），调用 `CreditManager.mark_depleted()`
   - 然后重新获取下一个可用凭证，重试请求
   - 最大重试次数 = 凭证总数
   - 全部重试失败后返回 HTTP 503："所有 CodeBuddy 账号积分已耗尽"

2. 在 `CodeBuddyStreamService.handle_stream_response()` 中：
   - 流式响应返回非 200 时，检查错误信息是否为积分相关
   - 如果是，返回包含积分耗尽提示的 SSE 错误事件

**错误检测关键词**（从上游响应中匹配）:
- HTTP 状态码：429、403
- 响应体关键词：通过 `error.data.msg` 中的关键词判断

**降级流程**:
```
请求 → 选取凭证 → 发送请求
                          ↓
                    成功(200) → 正常返回
                    失败(非200) → 解析错误
                          ↓
                    积分相关错误 → mark_depleted → 获取下一个凭证 → 重试
                    其他错误 → 直接返回错误（不重试）
                          ↓
                    全部凭证 depleted → 503 "所有账号积分已耗尽"
```

### 模块 4: 前端展示（修改 `frontend/admin.html`）

**凭据卡片新增积分信息**:

在每个 `credential-item` 的 `credential-meta` 区域新增一行：
```
积分: 3500/4500 (77%)  [进度条]
```

- 进度条颜色：>50% 绿色，20%-50% 黄色，<20% 红色
- 积分为 0 时：显示红色 "已耗尽" 标签，头像变为红色叹号
- 积分未知时：显示 "查询中..." 或 "--"

**新增 API 端点**:

- `GET /codebuddy/v1/credits`: 返回所有凭证的积分信息
- `POST /codebuddy/v1/credits/refresh`: 手动触发积分刷新

**汇总统计**:

在凭证管理页面的统计区域新增：
- 所有账号总积分
- 所有账号剩余积分
- 已耗尽账号数量

**刷新按钮**:

在凭证列表头部新增 "刷新积分" 按钮，点击调用 `/codebuddy/v1/credits/refresh`。

## 新增 API 端点汇总

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/codebuddy/v1/credits` | 获取所有凭证积分信息 |
| POST | `/codebuddy/v1/credits/refresh` | 手动触发积分刷新 |

## 文件变更汇总

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/credit_manager.py` | 新建 | 积分查询、缓存、管理 |
| `src/codebuddy_token_manager.py` | 修改 | `get_next_credential()` 增加积分过滤 |
| `src/codebuddy_router.py` | 修改 | 请求失败自动降级 + 新增积分 API 端点 |
| `src/anthropic_router.py` | 修改 | 请求失败自动降级 |
| `frontend/admin.html` | 修改 | 凭据卡片显示积分、汇总统计、刷新按钮 |
| `web.py` | 修改 | lifespan 中启动后台积分刷新任务 |

## 不做的事

- 不做 token 自动刷新（存储了 refresh_token 但本次不实现）
- 不做基于积分权重的智能调度（保持现有轮询逻辑）
- 不做积分预警通知（如积分低于阈值发通知）
- 不修改现有的手动选择凭证逻辑
