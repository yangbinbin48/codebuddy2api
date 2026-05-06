# 积分感知的多账号凭证管理 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现积分查询、自动屏蔽耗尽账号、请求失败自动降级、前端展示积分信息

**Architecture:** 新建 `CreditManager` 模块负责积分查询和缓存；修改 `CodeBuddyTokenManager.get_next_credential()` 增加积分过滤；在 `chat_completions` 和 `messages` 路由中增加失败重试逻辑；前端凭据卡片新增积分进度条

**Tech Stack:** Python 3.8+ / FastAPI / httpx (async HTTP) / JavaScript (前端)

**设计文档:** `docs/superpowers/specs/2026-04-20-credit-aware-credential-management-design.md`

---

### Task 1: 创建 CreditManager 核心模块

**Files:**
- Create: `src/credit_manager.py`

- [ ] **Step 1: 创建 `src/credit_manager.py`**

```python
"""
Credit Manager - 管理CodeBuddy账号积分查询、缓存和状态追踪
"""
import json
import time
import logging
from typing import Dict, Optional, List, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# 积分相关错误的检测关键词
CREDIT_ERROR_KEYWORDS = [
    "insufficient", "quota", "credits", "配额", "积分",
    "no credits", "credit exhausted", "balance"
]


class CreditManager:
    """CodeBuddy 积分管理器"""

    def __init__(self):
        self._cache: Dict[int, Dict] = {}  # index -> credit info
        self._cache_ttl = 600  # 10 分钟缓存
        self._depleted_indices: set = set()  # 已耗尽的凭证索引

    async def query_credential_credits(self, credential_data: Dict, index: int) -> Optional[Dict]:
        """查询单个凭证的积分信息"""
        from config import get_codebuddy_api_endpoint
        from src.codebuddy_router import get_http_client

        api_endpoint = get_codebuddy_api_endpoint()
        url = f"{api_endpoint}/billing/meter/get-user-resource"

        bearer_token = credential_data.get('bearer_token')
        if not bearer_token:
            logger.warning(f"[CreditManager] Credential #{index} has no bearer_token, skip")
            return None

        # 构建请求体
        now = datetime.now()
        request_body = {
            "PageNumber": 1,
            "PageSize": 200,
            "ProductCode": "p_tcaca",
            "Status": [0, 3],
            "PackageEndTimeRangeBegin": now.strftime("%Y-%m-%d %H:%M:%S"),
            "PackageEndTimeRangeEnd": "2127-01-01 00:00:00"
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {bearer_token}"
        }

        try:
            client = await get_http_client()
            response = await client.post(url, json=request_body, headers=headers, timeout=30.0)

            if response.status_code != 200:
                logger.warning(f"[CreditManager] Query failed for #{index}: HTTP {response.status_code}")
                return None

            data = response.json()
            if data.get("code") != 0:
                logger.warning(f"[CreditManager] API error for #{index}: {data.get('msg')}")
                return None

            accounts = data.get("data", {}).get("Response", {}).get("Data", {}).get("Accounts", [])

            # 汇总活跃包（Status=0）的积分
            total_credits = 0
            remain_credits = 0
            used_credits = 0
            packages = []

            for acc in accounts:
                if acc.get("Status") == 0:
                    cap_remain = acc.get("CapacityRemain", 0)
                    cap_size = acc.get("CapacitySize", 0)
                    cap_used = acc.get("CapacityUsed", 0)
                    total_credits += cap_size
                    remain_credits += cap_remain
                    used_credits += cap_used
                    packages.append({
                        "name": acc.get("PackageName", "Unknown"),
                        "remain": cap_remain,
                        "total": cap_size,
                        "used": cap_used,
                        "cycle_end": acc.get("CycleEndTime", "")
                    })

            credit_info = {
                "index": index,
                "total_credits": total_credits,
                "remain_credits": remain_credits,
                "used_credits": used_credits,
                "packages": packages,
                "last_updated": int(time.time()),
                "is_depleted": remain_credits <= 0
            }

            # 更新缓存和耗尽状态
            self._cache[index] = credit_info
            if remain_credits <= 0:
                self._depleted_indices.add(index)
            else:
                self._depleted_indices.discard(index)

            logger.info(f"[CreditManager] #{index}: {remain_credits}/{total_credits} credits, "
                        f"{'DEPLETED' if remain_credits <= 0 else 'OK'}")
            return credit_info

        except Exception as e:
            logger.error(f"[CreditManager] Exception querying #{index}: {e}")
            return None

    async def refresh_all_credits(self):
        """刷新所有凭证的积分"""
        from src.codebuddy_token_manager import codebuddy_token_manager

        credentials = codebuddy_token_manager.get_all_credentials()
        if not credentials:
            logger.info("[CreditManager] No credentials to refresh")
            return

        logger.info(f"[CreditManager] Refreshing credits for {len(credentials)} credentials...")
        for i, cred in enumerate(credentials):
            try:
                await self.query_credential_credits(cred, i)
            except Exception as e:
                logger.error(f"[CreditManager] Failed to refresh #{i}: {e}")

        total_remain = sum(info.get("remain_credits", 0) for info in self._cache.values())
        total_size = sum(info.get("total_credits", 0) for info in self._cache.values())
        depleted_count = len(self._depleted_indices)
        logger.info(f"[CreditManager] Refresh complete: {total_remain}/{total_size} total, "
                    f"{depleted_count} depleted")

    def get_credits_info(self, index: int) -> Optional[Dict]:
        """获取指定凭证的积分信息（带缓存）"""
        cached = self._cache.get(index)
        if cached is None:
            return None
        # 检查缓存是否过期
        if time.time() - cached.get("last_updated", 0) > self._cache_ttl:
            return None
        return cached

    def get_all_credits_info(self) -> List[Dict]:
        """获取所有凭证的积分信息"""
        return [self._cache.get(i) for i in sorted(self._cache.keys())]

    def get_all_credits_summary(self) -> Dict:
        """获取所有凭证的积分汇总"""
        total_remain = 0
        total_size = 0
        total_used = 0
        depleted_count = 0
        for info in self._cache.values():
            total_remain += info.get("remain_credits", 0)
            total_size += info.get("total_credits", 0)
            total_used += info.get("used_credits", 0)
            if info.get("is_depleted"):
                depleted_count += 1
        return {
            "total_credits": total_size,
            "remain_credits": total_remain,
            "used_credits": total_used,
            "total_accounts": len(self._cache),
            "depleted_accounts": depleted_count
        }

    def is_depleted(self, index: int) -> bool:
        """检查凭证是否积分耗尽"""
        return index in self._depleted_indices

    def mark_depleted(self, index: int):
        """标记凭证为积分耗尽"""
        self._depleted_indices.add(index)
        if index in self._cache:
            self._cache[index]["is_depleted"] = True
            self._cache[index]["remain_credits"] = 0
        logger.warning(f"[CreditManager] Credential #{index} marked as depleted")

    def mark_available(self, index: int):
        """标记凭证为可用"""
        self._depleted_indices.discard(index)
        if index in self._cache:
            self._cache[index]["is_depleted"] = False
        logger.info(f"[CreditManager] Credential #{index} marked as available")

    def clear_depleted(self, index: int):
        """清除凭证的耗尽状态（用于刷新时）"""
        self._depleted_indices.discard(index)

    @staticmethod
    def is_credit_related_error(status_code: int, error_body: str) -> bool:
        """判断错误是否为积分相关"""
        if status_code not in (403, 429):
            return False
        error_lower = error_body.lower()
        return any(kw in error_lower for kw in CREDIT_ERROR_KEYWORDS)


# 全局实例
credit_manager = CreditManager()
```

- [ ] **Step 2: 验证模块可导入**

Run: `python -c "from src.credit_manager import credit_manager; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/credit_manager.py
git commit -m "feat: add CreditManager module for credit query and caching"
```

---

### Task 2: 在 TokenManager 中集成积分过滤

**Files:**
- Modify: `src/codebuddy_token_manager.py:151-169`

- [ ] **Step 1: 修改 `get_next_credential()` 增加积分过滤**

在 `src/codebuddy_token_manager.py` 的 `get_next_credential` 方法中，在过滤过期凭证的循环里同时过滤积分耗尽的凭证。

找到 `get_next_credential` 方法中的这段代码（约第 158-165 行）：

```python
        # 过滤掉过期的凭证
        valid_credentials = []
        for i, cred in enumerate(self.credentials):
            if not self.is_token_expired(cred['data']):
                valid_credentials.append((i, cred))
            else:
                filename = os.path.basename(cred['file_path'])
                logger.warning(f"Skipping expired credential: {filename}")
```

替换为：

```python
        # 过滤掉过期和积分耗尽的凭证
        from .credit_manager import credit_manager
        valid_credentials = []
        for i, cred in enumerate(self.credentials):
            if self.is_token_expired(cred['data']):
                filename = os.path.basename(cred['file_path'])
                logger.warning(f"Skipping expired credential: {filename}")
            elif credit_manager.is_depleted(i):
                filename = os.path.basename(cred['file_path'])
                logger.warning(f"Skipping depleted credential: {filename}")
            else:
                valid_credentials.append((i, cred))
```

- [ ] **Step 2: 验证无语法错误**

Run: `python -c "from src.codebuddy_token_manager import codebuddy_token_manager; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/codebuddy_token_manager.py
git commit -m "feat: integrate credit filtering into credential rotation"
```

---

### Task 3: 在 web.py 中启动后台积分刷新

**Files:**
- Modify: `web.py:28-54`

- [ ] **Step 1: 在 lifespan 中启动后台积分刷新任务**

在 `web.py` 的 `lifespan` 函数中，在 `lifecycle_manager.startup()` 之后添加后台积分刷新。

找到这段代码（约第 48-49 行）：

```python
        # 启动时初始化资源（包含连接预热）
        await lifecycle_manager.startup()
        yield
```

替换为：

```python
        # 启动时初始化资源（包含连接预热）
        await lifecycle_manager.startup()

        # 启动后台积分刷新任务
        from src.credit_manager import credit_manager
        import asyncio
        credit_refresh_task = asyncio.create_task(_periodic_credit_refresh(credit_manager))
        logger.info("Background credit refresh task started")

        yield

        # 关闭时取消后台任务
        credit_refresh_task.cancel()
        try:
            await credit_refresh_task
        except asyncio.CancelledError:
            pass
```

然后在 `web.py` 的 `logger = logging.getLogger(__name__)` 之后（约第 25 行后）添加后台刷新函数：

```python
async def _periodic_credit_refresh(credit_manager, interval: int = 600):
    """后台定时刷新积分，间隔默认 10 分钟"""
    import asyncio
    await asyncio.sleep(30)  # 启动后 30 秒首次刷新
    while True:
        try:
            await credit_manager.refresh_all_credits()
        except Exception as e:
            logger.error(f"Background credit refresh failed: {e}")
        await asyncio.sleep(interval)
```

- [ ] **Step 2: 验证无语法错误**

Run: `python -c "import web; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add web.py
git commit -m "feat: start background credit refresh task on startup"
```

---

### Task 4: 添加积分 API 端点

**Files:**
- Modify: `src/codebuddy_router.py` (在文件末尾追加)

- [ ] **Step 1: 在 `codebuddy_router.py` 末尾添加积分端点**

在 `src/codebuddy_router.py` 文件末尾（第 893 行之后）追加：

```python


# --- 积分管理端点 ---

@router.get("/v1/credits")
async def get_credits(_token: str = Depends(authenticate)):
    """获取所有凭证的积分信息"""
    try:
        from .credit_manager import credit_manager
        all_info = credit_manager.get_all_credits_info()
        summary = credit_manager.get_all_credits_summary()
        return {
            "summary": summary,
            "credentials": all_info
        }
    except Exception as e:
        logger.error(f"获取积分信息失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v1/credits/refresh")
async def refresh_credits(_token: str = Depends(authenticate)):
    """手动触发积分刷新"""
    try:
        from .credit_manager import credit_manager
        await credit_manager.refresh_all_credits()
        all_info = credit_manager.get_all_credits_info()
        summary = credit_manager.get_all_credits_summary()
        return {
            "message": "积分刷新完成",
            "summary": summary,
            "credentials": all_info
        }
    except Exception as e:
        logger.error(f"积分刷新失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **Step 2: 验证无语法错误**

Run: `python -c "from src.codebuddy_router import router; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/codebuddy_router.py
git commit -m "feat: add credit query and refresh API endpoints"
```

---

### Task 5: 在 chat_completions 路由中实现请求失败自动降级

**Files:**
- Modify: `src/codebuddy_router.py:679-730`

- [ ] **Step 1: 修改 `chat_completions` 函数，增加失败重试逻辑**

找到 `chat_completions` 函数（第 679-730 行），将整个函数体替换为：

```python
@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    x_conversation_id: Optional[str] = Header(None, alias="X-Conversation-ID"),
    x_conversation_request_id: Optional[str] = Header(None, alias="X-Conversation-Request-ID"),
    x_conversation_message_id: Optional[str] = Header(None, alias="X-Conversation-Message-ID"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-ID"),
    _token: str = Depends(authenticate)
):
    """CodeBuddy V1 聊天完成API - 带积分耗尽自动降级"""
    try:
        # 解析和验证请求体
        try:
            request_body = await request.json()
        except Exception as e:
            logger.error(f"解析请求体失败: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid JSON request body: {str(e)}")

        # 验证请求参数
        RequestProcessor.validate_request(request_body)

        # 预处理请求（只需做一次）
        payload = RequestProcessor.prepare_payload(request_body)
        usage_stats_manager.record_model_usage(payload.get("model", "unknown"))
        client_wants_stream = request_body.get("stream", False)

        # 获取凭证总数用于最大重试次数
        from .credit_manager import credit_manager
        from src.codebuddy_token_manager import codebuddy_token_manager
        max_retries = len(codebuddy_token_manager.credentials)

        for attempt in range(max_retries):
            # 获取有效凭证
            credential = CredentialManager.get_valid_credential()

            # 生成请求头
            headers = codebuddy_api_client.generate_codebuddy_headers(
                bearer_token=credential.get('bearer_token'),
                user_id=credential.get('user_id'),
                conversation_id=x_conversation_id,
                conversation_request_id=x_conversation_request_id,
                conversation_message_id=x_conversation_message_id,
                request_id=x_request_id
            )

            # 使用服务类处理请求
            service = CodeBuddyStreamService()

            try:
                if client_wants_stream:
                    return await service.handle_stream_response(payload, headers)
                else:
                    return await service.handle_non_stream_response(payload, headers)
            except HTTPException as e:
                # 检查是否为积分相关错误
                error_detail = str(e.detail) if e.detail else ""
                if e.status_code in (403, 429) and credit_manager.is_credit_related_error(e.status_code, error_detail):
                    # 找到当前凭证索引并标记为耗尽
                    current_index = codebuddy_token_manager.current_index
                    credit_manager.mark_depleted(current_index)
                    logger.warning(f"Credit exhausted for credential #{current_index}, retrying... (attempt {attempt + 1}/{max_retries})")

                    if attempt < max_retries - 1:
                        continue  # 重试下一个凭证
                    else:
                        raise HTTPException(
                            status_code=503,
                            detail="所有 CodeBuddy 账号积分已耗尽，请充值或添加新账号"
                        )
                else:
                    raise  # 非积分错误，直接抛出

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"CodeBuddy V1 API错误: {e}")
        raise HTTPException(status_code=500, detail=f"内部服务器错误: {str(e)}")
```

- [ ] **Step 2: 验证无语法错误**

Run: `python -c "from src.codebuddy_router import router; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/codebuddy_router.py
git commit -m "feat: add auto-fallback on credit exhaustion for chat completions"
```

---

### Task 6: 在 anthropic messages 路由中实现请求失败自动降级

**Files:**
- Modify: `src/anthropic_router.py:56-106`

- [ ] **Step 1: 修改 `messages` 函数，增加失败重试逻辑**

找到 `messages` 函数（第 56-106 行），将整个函数体替换为：

```python
@router.post("/v1/messages")
async def messages(
    request: Request,
    _token: str = Depends(authenticate_anthropic),
):
    """Anthropic Messages API 端点 - 带积分耗尽自动降级"""
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

        # 预处理载荷 (设置 stream=True, 确保 2+ 消息, 关键词替换)
        payload = RequestProcessor.prepare_payload(openai_request)

        model = requested_model
        usage_stats_manager.record_model_usage(upstream_model)
        wants_stream = request_body.get("stream", False)
        estimated_input_tokens = _estimate_input_tokens(payload)

        # 获取凭证总数用于最大重试次数
        from src.credit_manager import credit_manager
        from src.codebuddy_token_manager import codebuddy_token_manager
        max_retries = len(codebuddy_token_manager.credentials)

        for attempt in range(max_retries):
            # 获取凭证
            credential = CredentialManager.get_valid_credential()

            # 生成请求头
            headers = codebuddy_api_client.generate_codebuddy_headers(
                bearer_token=credential.get('bearer_token'),
                user_id=credential.get('user_id'),
            )

            try:
                if wants_stream:
                    return await _handle_stream(payload, headers, model, estimated_input_tokens)
                else:
                    return await _handle_non_stream(payload, headers, model, estimated_input_tokens)
            except HTTPException as e:
                # 检查是否为积分相关错误
                error_detail = str(e.detail) if e.detail else ""
                if e.status_code in (403, 429) and credit_manager.is_credit_related_error(e.status_code, error_detail):
                    current_index = codebuddy_token_manager.current_index
                    credit_manager.mark_depleted(current_index)
                    logger.warning(f"Credit exhausted for credential #{current_index} (anthropic), retrying...")

                    if attempt < max_retries - 1:
                        continue
                    else:
                        _anthropic_error(503, "api_error", "所有 CodeBuddy 账号积分已耗尽，请充值或添加新账号")
                else:
                    raise

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Anthropic Messages API error: {e}")
        _anthropic_error(500, "api_error", str(e))
```

注意：需要确认 `anthropic_router.py` 中是否已导入 `codebuddy_api_client` 和 `RequestProcessor`。查看文件头部导入：

如果缺少 `from src.codebuddy_api_client import codebuddy_api_client`，需要添加。同样检查 `from src.codebuddy_token_manager import codebuddy_token_manager` 是否已存在。

- [ ] **Step 2: 验证无语法错误**

Run: `python -c "from src.anthropic_router import router; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/anthropic_router.py
git commit -m "feat: add auto-fallback on credit exhaustion for anthropic messages"
```

---

### Task 7: 前端 - 凭证卡片显示积分信息

**Files:**
- Modify: `frontend/admin.html` (多处)

- [ ] **Step 1: 在 `displayCredentials()` 函数的分组渲染中添加积分显示**

在 `frontend/admin.html` 中找到 `displayCredentials` 函数内分组渲染的凭据卡片（约第 2239-2280 行），在 `credential-meta` 区域的过期时间行之后添加积分显示行。

找到这段代码（约第 2255-2258 行）：

```javascript
                                        <div class="credential-meta-item">
                                            <i class="fas fa-clock"></i> 剩余: ${remainingTime}
                                        </div>
```

在其后插入：

```javascript
                                        ${cred.credits_info ?
                                            `<div class="credential-meta-item" style="flex-direction: column; gap: 4px;">
                                                <div style="display: flex; align-items: center; gap: 6px;">
                                                    <i class="fas fa-coins"></i>
                                                    积分: <strong>${cred.credits_info.remain_credits}</strong>/${cred.credits_info.total_credits}
                                                    <span style="font-size: 0.75rem; color: ${_creditColor(cred.credits_info.remain_credits, cred.credits_info.total_credits)};">
                                                        (${_creditPercent(cred.credits_info.remain_credits, cred.credits_info.total_credits)}%)
                                                    </span>
                                                    ${cred.credits_info.is_depleted ? '<span style="color: var(--error-color); font-weight: 600;">[已耗尽]</span>' : ''}
                                                </div>
                                                <div style="width: 100%; height: 4px; background: var(--border-color); border-radius: 2px; overflow: hidden;">
                                                    <div style="height: 100%; width: ${_creditPercent(cred.credits_info.remain_credits, cred.credits_info.total_credits)}%; background: ${_creditBarColor(cred.credits_info.remain_credits, cred.credits_info.total_credits)}; border-radius: 2px; transition: width 0.3s;"></div>
                                                </div>
                                            </div>` :
                                            `<div class="credential-meta-item">
                                                <i class="fas fa-coins"></i> 积分: 查询中...
                                            </div>`
                                        }
```

- [ ] **Step 2: 在 `loadCredentials()` 函数中加载积分数据**

找到 `loadCredentials` 函数（约第 2037-2061 行），在 `displayCredentials()` 调用之后添加积分加载：

找到这段代码（约第 2049-2051 行）：

```javascript
                    // 初始化凭证缓存
                    credentialsCache = (data.credentials || []).map(cred => ({...cred, status: 'unknown'}));
                    displayCredentials();
                    loadCurrentCredentialStatus(); // 加载当前状态
```

替换为：

```javascript
                    // 初始化凭证缓存
                    credentialsCache = (data.credentials || []).map(cred => ({...cred, status: 'unknown'}));
                    displayCredentials();
                    loadCurrentCredentialStatus(); // 加载当前状态
                    loadCreditsInfo(); // 加载积分信息
```

- [ ] **Step 3: 添加积分相关的辅助函数和加载函数**

在 `loadCredentials` 函数之前（约第 2035 行前）添加以下函数：

```javascript
        // --- 积分相关辅助函数 ---
        function _creditPercent(remain, total) {
            if (!total || total <= 0) return 0;
            return Math.round((remain / total) * 100);
        }

        function _creditColor(remain, total) {
            const pct = _creditPercent(remain, total);
            if (pct > 50) return 'var(--success-color)';
            if (pct > 20) return 'var(--warning-color)';
            return 'var(--error-color)';
        }

        function _creditBarColor(remain, total) {
            const pct = _creditPercent(remain, total);
            if (pct > 50) return '#10b981';
            if (pct > 20) return '#f59e0b';
            return '#ef4444';
        }

        async function loadCreditsInfo() {
            try {
                const response = await fetch('/codebuddy/v1/credits', {
                    headers: getAuthHeaders()
                });
                if (response.ok) {
                    const data = await response.json();
                    const creditsList = data.credentials || [];
                    // 将积分信息合并到凭证缓存
                    creditsList.forEach(info => {
                        if (info !== null && info !== undefined && credentialsCache[info.index]) {
                            credentialsCache[info.index].credits_info = info;
                        }
                    });
                    displayCredentials(); // 重新渲染以显示积分
                }
            } catch (error) {
                console.error('Failed to load credits info:', error);
            }
        }

        async function refreshCredits() {
            try {
                showNotification('正在刷新积分...', 'info');
                const response = await fetch('/codebuddy/v1/credits/refresh', {
                    method: 'POST',
                    headers: getAuthHeaders()
                });
                if (response.ok) {
                    const data = await response.json();
                    const creditsList = data.credentials || [];
                    creditsList.forEach(info => {
                        if (info !== null && info !== undefined && credentialsCache[info.index]) {
                            credentialsCache[info.index].credits_info = info;
                        }
                    });
                    displayCredentials();
                    showNotification(`积分刷新完成: ${data.summary?.remain_credits ?? '?'}/${data.summary?.total_credits ?? '?'}`, 'success');
                } else {
                    showNotification('积分刷新失败', 'error');
                }
            } catch (error) {
                showNotification(`积分刷新失败: ${error.message}`, 'error');
            }
        }
```

- [ ] **Step 4: 在已保存凭证卡片头部添加"刷新积分"按钮**

找到已保存凭证的卡片头部（约第 1466-1473 行）：

```html
                        <div>
                            <button id="rotationToggleBtn" class="btn btn-secondary" onclick="toggleAutoRotation()" style="margin-right: 0.5rem;">
                                <i class="fas fa-ban"></i> 恢复自动轮换
                            </button>
                            <button class="btn btn-primary" onclick="loadCredentials()">
                                <i class="fas fa-sync-alt"></i> 刷新列表
                            </button>
                        </div>
```

替换为：

```html
                        <div>
                            <button id="rotationToggleBtn" class="btn btn-secondary" onclick="toggleAutoRotation()" style="margin-right: 0.5rem;">
                                <i class="fas fa-ban"></i> 恢复自动轮换
                            </button>
                            <button class="btn btn-secondary" onclick="refreshCredits()" style="margin-right: 0.5rem;">
                                <i class="fas fa-coins"></i> 刷新积分
                            </button>
                            <button class="btn btn-primary" onclick="loadCredentials()">
                                <i class="fas fa-sync-alt"></i> 刷新列表
                            </button>
                        </div>
```

- [ ] **Step 5: 验证前端无语法错误**

在浏览器中打开管理面板，确认：
- 凭证列表正常显示
- "刷新积分"按钮出现
- 点击"刷新积分"后积分信息出现在每个凭据卡片中

- [ ] **Step 6: Commit**

```bash
git add frontend/admin.html
git commit -m "feat: display credit info in credential cards with progress bar"
```

---

### Task 8: 前端 - Dashboard 汇总统计显示总积分

**Files:**
- Modify: `frontend/admin.html` (Dashboard 统计区域)

- [ ] **Step 1: 在凭证统计卡片中添加积分汇总信息**

找到凭证统计卡片（约第 1236-1255 行），在 `stat-trend` 之后添加积分汇总行。

找到这段代码（约第 1252-1254 行）：

```html
                        <div class="stat-trend positive" id="credentialTrend">
                            <i class="fas fa-check"></i> <span id="validCredentials">0</span> 个有效
                        </div>
```

替换为：

```html
                        <div class="stat-trend positive" id="credentialTrend">
                            <i class="fas fa-check"></i> <span id="validCredentials">0</span> 个有效
                        </div>
                        <div class="stat-trend" id="creditsSummaryTrend" style="margin-top: 4px; font-size: 0.75rem;">
                            <i class="fas fa-coins"></i> 积分: <span id="creditsSummaryText">加载中...</span>
                        </div>
```

- [ ] **Step 2: 在 Dashboard 加载逻辑中添加积分汇总更新**

找到 Dashboard 的凭证统计加载逻辑（约第 1896 行之后），在 `credTrend.innerHTML = ...` 的 if/else 块结束后添加：

找到这段代码（约第 1916-1920 行）：

```javascript
                    } else {
                        document.getElementById('totalCredentials').textContent = 'N/A';
                        document.getElementById('validCredentials').textContent = '0';
                    }
```

替换为：

```javascript
                    } else {
                        document.getElementById('totalCredentials').textContent = 'N/A';
                        document.getElementById('validCredentials').textContent = '0';
                    }

                    // 加载积分汇总
                    try {
                        const creditsResp = await fetch('/codebuddy/v1/credits', {
                            headers: getAuthHeaders()
                        });
                        if (creditsResp.ok) {
                            const creditsData = await creditsResp.json();
                            const summary = creditsData.summary || {};
                            const remain = summary.remain_credits ?? '?';
                            const total = summary.total_credits ?? '?';
                            const depleted = summary.depleted_accounts ?? 0;
                            const creditsText = document.getElementById('creditsSummaryText');
                            creditsText.textContent = `${remain}/${total}`;
                            const trend = document.getElementById('creditsSummaryTrend');
                            if (depleted > 0) {
                                trend.className = 'stat-trend negative';
                                trend.innerHTML = `<i class="fas fa-coins"></i> 积分: <span id="creditsSummaryText">${remain}/${total}</span> (${depleted}个耗尽)`;
                            } else {
                                trend.className = 'stat-trend positive';
                                trend.innerHTML = `<i class="fas fa-coins"></i> 积分: <span id="creditsSummaryText">${remain}/${total}</span>`;
                            }
                        }
                    } catch (e) {
                        console.error('Failed to load credits summary:', e);
                    }
```

- [ ] **Step 3: 验证**

在浏览器中打开管理面板，确认 Dashboard 的凭证统计卡片下方显示积分汇总。

- [ ] **Step 4: Commit**

```bash
git add frontend/admin.html
git commit -m "feat: show credit summary in dashboard statistics"
```

---

### Task 9: 集成测试与验收

- [ ] **Step 1: 启动服务，验证基本功能**

Run: `python web.py`

验证以下功能：
1. 服务正常启动，日志显示 "Background credit refresh task started"
2. 30 秒后日志显示积分查询结果（每个凭证的积分信息）
3. 打开管理面板 → Dashboard 显示积分汇总
4. 凭证管理 → 每个凭据卡片显示积分进度条
5. 点击"刷新积分"按钮，积分信息更新

- [ ] **Step 2: 验证积分耗尽自动降级**

1. 在管理面板手动选择一个凭证
2. 通过 API 发送请求（使用 Claude Code 或 curl）
3. 如果该凭证积分耗尽，应自动切换到下一个凭证
4. 所有凭证耗尽时，应返回 503 "所有 CodeBuddy 账号积分已耗尽"

- [ ] **Step 3: Final Commit**

```bash
git add -A
git commit -m "feat: credit-aware credential management - complete implementation"
```
