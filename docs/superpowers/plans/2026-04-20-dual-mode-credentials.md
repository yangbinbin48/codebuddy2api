# 双模式凭证支持 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持官方版和企业版 CodeBuddy 凭证同时存在、同时使用，每个凭证独立携带自己的 API 端点和企业 ID。

**Architecture:** 在凭证 JSON 中新增 `api_endpoint` 和 `enterprise_id` 字段，所有 API 调用（认证、聊天、积分查询）从当前凭证数据中读取这些字段，而非全局配置。前端认证流程增加类型选择器。

**Tech Stack:** Python 3.8+ / FastAPI / httpx / Vanilla JS (frontend)

---

### Task 1: 移除全局配置并清理 config.py

**Files:**
- Modify: `config.py`

- [ ] **Step 1: 从 `_DEFAULT_CONFIG` 中移除 `CODEBUDDY_API_ENDPOINT` 和 `CODEBUDDY_ENTERPRISE_ID`**

修改 `config.py` 第 22-34 行，删除这两个键：

```python
_DEFAULT_CONFIG = {
    "CODEBUDDY_HOST": "127.0.0.1",
    "CODEBUDDY_PORT": 8010,
    "CODEBUDDY_PASSWORD": None,
    "CODEBUDDY_CREDS_DIR": ".codebuddy_creds",
    "CODEBUDDY_LOG_LEVEL": "INFO",
    "CODEBUDDY_MODELS": "claude-4.0,claude-3.7,gpt-5,gpt-5-mini,gpt-5-nano,o4-mini,gemini-2.5-flash,gemini-2.5-pro,auto-chat",
    "CODEBUDDY_ROTATION_COUNT": 1,
    "CODEBUDDY_PROXY": None,
    "CODEBUDDY_AUTH_TIMEOUT": 30,
}
```

- [ ] **Step 2: 删除 `get_codebuddy_api_endpoint()` 和 `get_enterprise_id()` 函数**

删除 `config.py` 第 121-138 行的两个函数。

- [ ] **Step 3: 启动项目确认无导入错误**

Run: `cd D:/code2/codebuddy2api && python -c "from config import load_config; load_config(); print('OK')"`
Expected: 输出 "OK"

- [ ] **Step 4: Commit**

```bash
git add config.py
git commit -m "refactor: remove CODEBUDDY_API_ENDPOINT and CODEBUDDY_ENTERPRISE_ID from global config"
```

---

### Task 2: 凭证自动迁移 - token_manager.py

**Files:**
- Modify: `src/codebuddy_token_manager.py`

- [ ] **Step 1: 在 `load_all_tokens()` 中添加字段自动填充和回写逻辑**

在 `load_all_tokens()` 方法中，`self.credentials.append(...)` 之后（约第 55 行），添加字段检测和回写逻辑：

```python
                        # 自动迁移：填充缺失的 api_endpoint 和 enterprise_id
                        needs_save = False
                        if 'api_endpoint' not in data:
                            data['api_endpoint'] = 'https://www.codebuddy.ai'
                            needs_save = True
                        if 'enterprise_id' not in data:
                            data['enterprise_id'] = None
                            needs_save = True
                        if needs_save:
                            try:
                                with open(file_path, 'w', encoding='utf-8') as wf:
                                    json.dump(data, wf, indent=4, ensure_ascii=False)
                                logger.info(f"Migrated credential {os.path.basename(file_path)}: added api_endpoint and enterprise_id")
                            except Exception as write_err:
                                logger.warning(f"Failed to write migration for {os.path.basename(file_path)}: {write_err}")
```

- [ ] **Step 2: 在 `get_credentials_info()` 返回值中包含 `api_endpoint` 和 `enterprise_id`**

在 `get_credentials_info()` 方法中（约第 262 行的 info dict），添加：

```python
                'api_endpoint': data.get('api_endpoint', 'https://www.codebuddy.ai'),
                'enterprise_id': data.get('enterprise_id'),
```

- [ ] **Step 3: 启动项目确认凭证加载和迁移正常**

Run: `cd D:/code2/codebuddy2api && python -c "from src.codebuddy_token_manager import codebuddy_token_manager; print(f'Loaded {len(codebuddy_token_manager.credentials)} credentials'); creds = codebuddy_token_manager.get_credentials_info(); print(f'First credential api_endpoint: {creds[0].get(\"api_endpoint\") if creds else \"N/A\"}'); print(f'First credential enterprise_id: {creds[0].get(\"enterprise_id\") if creds else \"N/A\"}')"`
Expected: 显示已加载凭证数量和迁移后的字段值

- [ ] **Step 4: Commit**

```bash
git add src/codebuddy_token_manager.py
git commit -m "feat: auto-migrate credentials with api_endpoint and enterprise_id fields"
```

---

### Task 3: 认证流程支持类型参数 - codebuddy_auth_router.py

**Files:**
- Modify: `src/codebuddy_auth_router.py`

- [ ] **Step 1: 重构 `_get_base_url()` 和 `get_auth_start_headers()` / `get_auth_poll_headers()` 接受参数**

替换 `_get_base_url()` 函数（第 26-29 行）：

```python
def _get_base_url(api_endpoint: str = None) -> str:
    """获取CodeBuddy基础URL，优先使用传入的api_endpoint"""
    if api_endpoint:
        return api_endpoint
    # 向后兼容：如果没有传入，使用官方默认值
    return 'https://www.codebuddy.ai'
```

替换 `get_auth_start_headers()` 函数（第 103-134 行）：

```python
def get_auth_start_headers(enterprise_id: str = None, api_endpoint: str = None) -> Dict[str, str]:
    """生成启动认证(/state)所需的请求头"""
    request_id = str(uuid.uuid4()).replace('-', '')
    host = _get_host_from_url(_get_base_url(api_endpoint))
    is_enterprise = bool(enterprise_id)

    headers = {
        'Host': host,
        'Accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'Connection': 'close',
        'X-Requested-With': 'XMLHttpRequest',
        'X-Domain': host,
        'User-Agent': 'VSCode/1.115.0 H3CAICODE/4.2.22590715' if is_enterprise else 'CLI/1.0.8 CodeBuddy/1.0.8',
        'X-Product': 'Cloud-Hosted' if is_enterprise else 'SaaS',
        'X-Request-ID': request_id,
    }

    if is_enterprise:
        headers['X-Enterprise-Id'] = enterprise_id
        headers['X-Tenant-Id'] = enterprise_id
        headers['X-Env-ID'] = 'production'
    else:
        headers['X-No-Authorization'] = 'true'
        headers['X-No-User-Id'] = 'true'
        headers['X-No-Enterprise-Id'] = 'true'
        headers['X-No-Department-Info'] = 'true'

    return headers
```

替换 `get_auth_poll_headers()` 函数（第 136-172 行）：

```python
def get_auth_poll_headers(enterprise_id: str = None, api_endpoint: str = None) -> Dict[str, str]:
    """生成轮询认证(/token)所需的请求头"""
    request_id = str(uuid.uuid4()).replace('-', '')
    span_id = secrets.token_hex(8)
    host = _get_host_from_url(_get_base_url(api_endpoint))
    is_enterprise = bool(enterprise_id)

    headers = {
        'Host': host,
        'Accept': 'application/json, text/plain, */*',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'Connection': 'close',
        'X-Requested-With': 'XMLHttpRequest',
        'X-Request-ID': request_id,
        'b3': f'{request_id}-{span_id}-1-',
        'X-B3-TraceId': request_id,
        'X-B3-ParentSpanId': '',
        'X-B3-SpanId': span_id,
        'X-B3-Sampled': '1',
        'X-Domain': host,
        'User-Agent': 'VSCode/1.115.0 H3CAICODE/4.2.22590715' if is_enterprise else 'CLI/1.0.8 CodeBuddy/1.0.8',
        'X-Product': 'Cloud-Hosted' if is_enterprise else 'SaaS',
    }

    if is_enterprise:
        headers['X-Enterprise-Id'] = enterprise_id
        headers['X-Tenant-Id'] = enterprise_id
        headers['X-Env-ID'] = 'production'
    else:
        headers['X-No-Authorization'] = 'true'
        headers['X-No-User-Id'] = 'true'
        headers['X-No-Enterprise-Id'] = 'true'
        headers['X-No-Department-Info'] = 'true'

    return headers
```

- [ ] **Step 2: 重构 `start_codebuddy_auth()` 接受参数**

替换 `start_codebuddy_auth()` 函数（第 174-257 行）的签名和内部调用：

```python
async def start_codebuddy_auth(enterprise_id: str = None, api_endpoint: str = None) -> Dict[str, Any]:
    """启动CodeBuddy认证流程"""
    try:
        logger.info("启动CodeBuddy认证流程...")

        headers = get_auth_start_headers(enterprise_id=enterprise_id, api_endpoint=api_endpoint)
        base_url = _get_base_url(api_endpoint)
        token_endpoint = f'{base_url}/v2/plugin/auth/token'
        state_endpoint = f'{base_url}/v2/plugin/auth/state'
        auth_timeout = _get_auth_timeout()
        # ... 后续代码不变，只需确保使用正确的 base_url 和 headers
```

注意：函数内部所有 `get_auth_start_headers()` 调用保持不变（已在上面修改了参数），`_get_auth_endpoints()` 内联替换为 `token_endpoint` 和 `state_endpoint` 的局部变量。函数内第 180-181 行的 `token_endpoint, state_endpoint = _get_auth_endpoints()` 和 `base_url = _get_base_url()` 改为上面已定义的局部变量。第 214-215 行的重试逻辑中 `get_auth_start_headers()` 调用也保持不变。

- [ ] **Step 3: 重构 `poll_codebuddy_auth_status()` 接受参数**

替换 `poll_codebuddy_auth_status()` 函数签名（第 259 行）：

```python
async def poll_codebuddy_auth_status(auth_state: str, enterprise_id: str = None, api_endpoint: str = None) -> Dict[str, Any]:
    """轮询CodeBuddy认证状态"""
    try:
        headers = get_auth_poll_headers(enterprise_id=enterprise_id, api_endpoint=api_endpoint)
        base_url = _get_base_url(api_endpoint)
        token_endpoint = f'{base_url}/v2/plugin/auth/token'
        url = f"{token_endpoint}?state={auth_state}"
        # ... 后续代码不变
```

注意：删除第 263 行的 `token_endpoint, _ = _get_auth_endpoints()`，使用上面的局部变量。

- [ ] **Step 4: 重构 `save_codebuddy_token()` 接受 api_endpoint 和 enterprise_id**

修改 `save_codebuddy_token()` 函数签名（第 322 行）：

```python
async def save_codebuddy_token(token_data: Dict[str, Any], api_endpoint: str = 'https://www.codebuddy.ai', enterprise_id: str = None) -> bool:
```

在构建 `credential_data` 的 dict 中（约第 393-405 行），添加：

```python
            "api_endpoint": api_endpoint,
            "enterprise_id": enterprise_id,
```

- [ ] **Step 5: 修改 API 端点 `/auth/start` 和 `/auth/poll`**

修改 `/auth/start` 端点（第 431 行），接受查询参数：

```python
@router.get("/auth/start", summary="Start CodeBuddy Authentication")
async def start_device_auth(
    auth_type: str = "official",
    enterprise_id: Optional[str] = None,
    api_endpoint: Optional[str] = None
):
    """启动CodeBuddy认证流程
    auth_type: "official" 或 "enterprise"
    enterprise_id: 企业标识（企业版必填）
    api_endpoint: API端点（企业版必填）
    """
    try:
        logger.info(f"开始启动CodeBuddy认证流程... type={auth_type}")

        # 参数验证
        if auth_type == "enterprise":
            if not enterprise_id:
                return {"success": False, "error": "missing_enterprise_id", "message": "企业版认证需要提供企业标识"}
            if not api_endpoint:
                return {"success": False, "error": "missing_api_endpoint", "message": "企业版认证需要提供API端点"}
        else:
            # 官方版使用默认值
            api_endpoint = 'https://www.codebuddy.ai'
            enterprise_id = None

        real_auth_result = await start_codebuddy_auth(
            enterprise_id=enterprise_id,
            api_endpoint=api_endpoint
        )

        if real_auth_result.get('success'):
            # 保存认证类型信息到 authData，供 poll 成功后使用
            real_auth_result['auth_type'] = auth_type
            real_auth_result['enterprise_id'] = enterprise_id
            real_auth_result['api_endpoint'] = api_endpoint
            logger.info("真实CodeBuddy认证API启动成功!")
            return real_auth_result
        else:
            logger.warning(f"真实认证API失败: {real_auth_result}")
            return real_auth_result

    except Exception as e:
        logger.error(f"认证启动过程发生异常: {e}")
        return {
            "success": False,
            "error": "Unexpected error",
            "message": f"认证启动失败: {str(e)}"
        }
```

修改 `/auth/poll` 端点（第 455 行），传递认证参数：

在 `poll_for_token()` 函数签名中添加参数：

```python
@router.post("/auth/poll", summary="Poll for OAuth token")
async def poll_for_token(
    device_code: str = Body(None, embed=True),
    code_verifier: str = Body(None, embed=True),
    auth_state: str = Body(None, embed=True),
    auth_type: str = Body(None, embed=True),
    enterprise_id: str = Body(None, embed=True),
    api_endpoint: str = Body(None, embed=True)
):
```

在 `if auth_state:` 分支中，修改 `poll_codebuddy_auth_status` 调用（约第 467 行）：

```python
            poll_result = await poll_codebuddy_auth_status(
                auth_state,
                enterprise_id=enterprise_id,
                api_endpoint=api_endpoint
            )
```

修改 `save_codebuddy_token` 调用（约第 477 行）：

```python
                    token_saved = await save_codebuddy_token(
                        token_data,
                        api_endpoint=api_endpoint or 'https://www.codebuddy.ai',
                        enterprise_id=enterprise_id
                    )
```

- [ ] **Step 6: Commit**

```bash
git add src/codebuddy_auth_router.py
git commit -m "feat: support auth type selection (official/enterprise) with per-credential endpoint"
```

---

### Task 4: API 客户端使用凭证级参数 - codebuddy_api_client.py

**Files:**
- Modify: `src/codebuddy_api_client.py`

- [ ] **Step 1: 修改 `generate_codebuddy_headers()` 接受 enterprise_id 和 api_endpoint 参数**

替换 `generate_codebuddy_headers()` 方法签名和内部逻辑（第 177-242 行）：

```python
    def generate_codebuddy_headers(
        self,
        bearer_token: str,
        user_id: str = None,
        conversation_id: Optional[str] = None,
        conversation_request_id: Optional[str] = None,
        conversation_message_id: Optional[str] = None,
        request_id: Optional[str] = None,
        enterprise_id: Optional[str] = None,
        api_endpoint: Optional[str] = None
    ) -> Dict[str, str]:
        """
        生成CodeBuddy API所需的完整请求头。
        根据传入的 enterprise_id 自动选择企业版或SaaS版请求头。
        """
        host = self._extract_host(api_endpoint) if api_endpoint else self._host
        is_enterprise = bool(enterprise_id)

        if is_enterprise:
            headers = {
                'Host': host,
                'Accept': 'application/json',
                'Content-Type': 'application/json;charset=UTF-8',
                'Authorization': f'Bearer {bearer_token}',
                'X-User-Id': user_id or '',
                'X-Enterprise-Id': enterprise_id,
                'X-Tenant-Id': enterprise_id,
                'X-Domain': host,
                'X-Product': 'Cloud-Hosted',
                'X-IDE-Type': 'VSCode',
                'X-IDE-Version': '1.115.0',
                'X-Product-Version': '4.2.22590715',
                'X-Env-ID': 'production',
                'User-Agent': 'VSCode/1.115.0 H3CAICODE/4.2.22590715',
                'X-Request-Trace-Id': request_id or str(uuid.uuid4()).replace('-', ''),
            }
        else:
            headers = {
                'Host': host,
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'X-Requested-With': 'XMLHttpRequest',
                'x-stainless-arch': 'x64',
                'x-stainless-lang': 'js',
                'x-stainless-os': 'Windows',
                'x-stainless-package-version': '5.10.1',
                'x-stainless-retry-count': '0',
                'x-stainless-runtime': 'node',
                'x-stainless-runtime-version': 'v22.13.1',
                'X-Conversation-ID': conversation_id or str(uuid.uuid4()),
                'X-Conversation-Request-ID': conversation_request_id or secrets.token_hex(16),
                'X-Conversation-Message-ID': conversation_message_id or str(uuid.uuid4()).replace('-', ''),
                'X-Request-ID': request_id or str(uuid.uuid4()).replace('-', ''),
                'X-Agent-Intent': 'craft',
                'X-IDE-Type': 'CLI',
                'X-IDE-Name': 'CLI',
                'X-IDE-Version': '1.0.7',
                'Authorization': f'Bearer {bearer_token}',
                'X-Domain': host,
                'User-Agent': 'CLI/1.0.7 CodeBuddy/1.0.7',
                'X-Product': 'SaaS',
                'X-User-Id': user_id or 'b5be3a67-237e-4ee6-9b9a-0b9ecd7b454b'
            }
        return headers
```

- [ ] **Step 2: Commit**

```bash
git add src/codebuddy_api_client.py
git commit -m "refactor: generate_codebuddy_headers accepts per-credential enterprise_id and api_endpoint"
```

---

### Task 5: 聊天路由使用凭证级端点 - codebuddy_router.py

**Files:**
- Modify: `src/codebuddy_router.py`

- [ ] **Step 1: 修改 `get_codebuddy_api_url()` 接受 api_endpoint 参数**

替换 `get_codebuddy_api_url()` 函数（第 29-35 行）：

```python
def get_codebuddy_api_url(api_endpoint: str = None) -> str:
    """获取 CodeBuddy API URL，支持按凭证传入不同的 endpoint"""
    if api_endpoint:
        return f"{api_endpoint}/v2/chat/completions"
    # 向后兼容：无参数时使用官方默认值
    return "https://www.codebuddy.ai/v2/chat/completions"
```

- [ ] **Step 2: 修改 `AppLifecycleManager.startup()` 中的预热逻辑**

替换 `startup()` 方法中的预热代码（约第 119-143 行），移除对已删除的 `get_codebuddy_api_endpoint()` 的依赖：

```python
        @staticmethod
        async def startup():
            """应用启动时的初始化"""
            logger.info("CodeBuddy Router 启动中...")
            # 创建 HTTP 客户端
            client = await get_http_client()

            # 预热连接：使用第一个凭证的 endpoint 进行连接预热
            try:
                from src.codebuddy_token_manager import codebuddy_token_manager

                # 尝试获取第一个凭证的 endpoint 进行预热
                endpoint = 'https://www.codebuddy.ai'  # 默认值
                if codebuddy_token_manager.credentials:
                    first_cred_data = codebuddy_token_manager.credentials[0]['data']
                    endpoint = first_cred_data.get('api_endpoint', endpoint)

                probe_url = f"{endpoint}/health"

                import socket
                from urllib.parse import urlparse
                parsed = urlparse(endpoint)
                hostname = parsed.hostname
                port = parsed.port or (443 if parsed.scheme == 'https' else 80)

                start = time.time()
                resolved = socket.getaddrinfo(hostname, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
                dns_ms = (time.time() - start) * 1000
                logger.info(f"DNS 预解析完成: {hostname} -> {resolved[0][4][0] if resolved else 'N/A'} ({dns_ms:.0f}ms)")

                start = time.time()
                probe_resp = await client.get(probe_url, timeout=httpx.Timeout(10.0, connect=5.0))
                connect_ms = (time.time() - start) * 1000
                logger.info(f"连接预热完成: {probe_resp.status_code} ({connect_ms:.0f}ms)")
            except Exception as e:
                logger.warning(f"连接预热失败（不影响服务）: {e}")

            logger.info("HTTP 连接池已初始化并预热")
```

- [ ] **Step 3: 修改 `chat_completions` 路由传递凭证级参数**

在 `chat_completions()` 函数中（约第 679 行），修改请求头生成和 API URL 调用：

找到 `headers = codebuddy_api_client.generate_codebuddy_headers(` （约第 715 行），修改为：

```python
            headers = codebuddy_api_client.generate_codebuddy_headers(
                bearer_token=credential.get('bearer_token'),
                user_id=credential.get('user_id'),
                conversation_id=x_conversation_id,
                conversation_request_id=x_conversation_request_id,
                conversation_message_id=x_conversation_message_id,
                request_id=x_request_id,
                enterprise_id=credential.get('enterprise_id'),
                api_endpoint=credential.get('api_endpoint')
            )
```

找到 `CodeBuddyStreamService()` 的使用处（约第 725-731 行），修改 `handle_stream_response` 和 `handle_non_stream_response` 调用以传入凭证的 `api_endpoint`：

修改 `handle_stream_response` 和 `handle_non_stream_response` 方法签名，增加 `api_endpoint` 参数：

```python
    async def handle_stream_response(self, payload: Dict[str, Any], headers: Dict[str, str], api_endpoint: str = None) -> StreamingResponse:
```

在 `stream_core()` 内部找到 `get_codebuddy_api_url()` 调用（约第 508 行），替换为：

```python
                async with client.stream("POST", get_codebuddy_api_url(api_endpoint), json=payload, headers=headers) as response:
```

同样修改 `handle_non_stream_response`：

```python
    async def handle_non_stream_response(self, payload: Dict[str, Any], headers: Dict[str, str], api_endpoint: str = None) -> Dict[str, Any]:
```

内部的 `get_codebuddy_api_url()` 调用（约第 577 行）改为 `get_codebuddy_api_url(api_endpoint)`。

在 `chat_completions` 路由中，修改 service 调用（约第 728-731 行）：

```python
            cred_api_endpoint = credential.get('api_endpoint')

            try:
                if client_wants_stream:
                    return await service.handle_stream_response(payload, headers, api_endpoint=cred_api_endpoint)
                else:
                    return await service.handle_non_stream_response(payload, headers, api_endpoint=cred_api_endpoint)
```

- [ ] **Step 4: Commit**

```bash
git add src/codebuddy_router.py
git commit -m "feat: chat completions uses per-credential api_endpoint and enterprise_id"
```

---

### Task 6: 积分管理使用凭证级参数 - credit_manager.py

**Files:**
- Modify: `src/credit_manager.py`

- [ ] **Step 1: 修改 `query_credential_credits()` 从凭证数据读取 endpoint 和 enterprise_id**

替换 `query_credential_credits()` 方法中的全局配置读取（第 27-68 行）：

```python
    async def query_credential_credits(self, credential_data: Dict, index: int) -> Optional[Dict]:
        """查询单个凭证的积分信息"""
        from src.codebuddy_router import get_http_client

        api_endpoint = credential_data.get('api_endpoint', 'https://www.codebuddy.ai')
        enterprise_id = credential_data.get('enterprise_id')
        url = f"{api_endpoint}/v2/billing/meter/get-user-resource"

        bearer_token = credential_data.get('bearer_token')
        if not bearer_token:
            logger.debug(f"[CreditManager] Credential #{index} has no bearer_token, skip")
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
            "Accept": "application/json, text/plain, */*",
            "Authorization": f"Bearer {bearer_token}"
        }

        # 企业版额外请求头
        if enterprise_id:
            headers["X-Enterprise-Id"] = enterprise_id
            headers["X-Tenant-Id"] = enterprise_id
        user_id = credential_data.get('user_id')
        if user_id:
            headers["X-User-Id"] = user_id
        domain = credential_data.get('domain')
        if domain:
            headers["X-Domain"] = domain

        # ... 后续代码不变
```

- [ ] **Step 2: Commit**

```bash
git add src/credit_manager.py
git commit -m "refactor: credit manager reads api_endpoint and enterprise_id from credential data"
```

---

### Task 7: Anthropic 路由使用凭证级参数 - anthropic_router.py

**Files:**
- Modify: `src/anthropic_router.py`

- [ ] **Step 1: 修改 `_handle_stream` 和 `_handle_non_stream` 接受 api_endpoint 参数**

修改 `_handle_stream` 函数签名（第 127 行）：

```python
async def _handle_stream(
    payload: dict,
    headers: dict,
    model: str,
    estimated_input_tokens: int = 0,
    api_endpoint: str = None,
) -> StreamingResponse:
```

内部 `get_codebuddy_api_url()` 调用（约第 141 行）改为 `get_codebuddy_api_url(api_endpoint)`。

修改 `_handle_non_stream` 函数签名（第 216 行）：

```python
async def _handle_non_stream(
    payload: dict,
    headers: dict,
    model: str,
    estimated_input_tokens: int = 0,
    api_endpoint: str = None,
) -> dict:
```

内部两处 `get_codebuddy_api_url()` 调用（约第 224 行和第 226 行）改为 `get_codebuddy_api_url(api_endpoint)`。

- [ ] **Step 2: 修改 `messages()` 路由传递凭证级参数**

在 `messages()` 函数中（约第 56 行），找到 `codebuddy_api_client.generate_codebuddy_headers(` 调用（约第 95 行），修改为：

```python
            headers = codebuddy_api_client.generate_codebuddy_headers(
                bearer_token=credential.get('bearer_token'),
                user_id=credential.get('user_id'),
                enterprise_id=credential.get('enterprise_id'),
                api_endpoint=credential.get('api_endpoint')
            )
```

找到 `_handle_stream` 和 `_handle_non_stream` 调用（约第 101-104 行），修改为：

```python
            try:
                cred_api_endpoint = credential.get('api_endpoint')
                if wants_stream:
                    return await _handle_stream(payload, headers, model, estimated_input_tokens, api_endpoint=cred_api_endpoint)
                else:
                    return await _handle_non_stream(payload, headers, model, estimated_input_tokens, api_endpoint=cred_api_endpoint)
```

- [ ] **Step 3: Commit**

```bash
git add src/anthropic_router.py
git commit -m "feat: anthropic router uses per-credential api_endpoint and enterprise_id"
```

---

### Task 8: 设置页面清理 - settings_router.py

**Files:**
- Modify: `src/settings_router.py`

- [ ] **Step 1: 从 SETTING_LABELS 中移除 CODEBUDDY_API_ENDPOINT 和 CODEBUDDY_ENTERPRISE_ID**

删除 `SETTING_LABELS` 中的这两个键（第 22 行和第 26 行附近）：

```python
SETTING_LABELS = {
    "CODEBUDDY_HOST": "服务主机地址",
    "CODEBUDDY_PORT": "服务端口",
    "CODEBUDDY_PASSWORD": "API 服务访问密码",
    "CODEBUDDY_CREDS_DIR": "凭证文件目录",
    "CODEBUDDY_LOG_LEVEL": "日志级别",
    "CODEBUDDY_MODELS": "可用模型列表 (逗号分隔)",
    "CODEBUDDY_ROTATION_COUNT": "凭证轮换频率 (N次请求/凭证，设为0关闭轮换)"
}
```

- [ ] **Step 2: Commit**

```bash
git add src/settings_router.py
git commit -m "refactor: remove CODEBUDDY_API_ENDPOINT and CODEBUDDY_ENTERPRISE_ID from settings"
```

---

### Task 9: 手动添加凭证 API 支持类型参数 - codebuddy_router.py

**Files:**
- Modify: `src/codebuddy_router.py`

- [ ] **Step 1: 修改 `/v1/credentials` POST 端点接受 api_endpoint 和 enterprise_id**

找到 `add_credential` 端点（约第 810 行），修改为：

```python
@router.post("/v1/credentials", summary="Add a new credential")
async def add_credential(
    request: Request,
    _token: str = Depends(authenticate)
):
    """添加一个新的认证凭证"""
    try:
        data = await request.json()
        if not data.get("bearer_token"):
            raise HTTPException(status_code=422, detail="bearer_token is required")

        # 支持凭证级配置
        api_endpoint = data.get("api_endpoint", "https://www.codebuddy.ai")
        enterprise_id = data.get("enterprise_id")  # 可为 None

        # 构建完整凭证数据
        credential_data = {
            "bearer_token": data.get("bearer_token"),
            "user_id": data.get("user_id"),
            "created_at": int(time.time()),
            "api_endpoint": api_endpoint,
            "enterprise_id": enterprise_id,
        }

        # 移除 None 值
        credential_data = {k: v for k, v in credential_data.items() if v is not None}

        success = codebuddy_token_manager.add_credential_with_data(
            credential_data=credential_data,
            filename=data.get("filename")
        )
        if not success:
            raise HTTPException(status_code=500, detail="Failed to save credential file")

        return {"message": "Credential added successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"添加凭证失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **Step 2: Commit**

```bash
git add src/codebuddy_router.py
git commit -m "feat: add credential API accepts api_endpoint and enterprise_id"
```

---

### Task 10: 前端 - 认证类型选择器 UI

**Files:**
- Modify: `frontend/admin.html`

- [ ] **Step 1: 在自动认证区域添加类型选择器 HTML**

在 `<!-- OAuth2自动认证区域 -->` 的 `<p>` 标签之后（约第 1386 行）、`开始认证` 按钮之前，添加类型选择器：

```html
                    <!-- 认证类型选择 -->
                    <div class="form-group" style="margin-bottom: 1rem;">
                        <label class="form-label" style="margin-bottom: 0.5rem;">认证类型</label>
                        <div style="display: flex; gap: 0.5rem;">
                            <label style="
                                display: flex; align-items: center; gap: 6px;
                                padding: 8px 16px; border: 2px solid var(--primary);
                                border-radius: 8px; cursor: pointer; flex: 1;
                                background: var(--primary-alpha);
                                transition: all 0.2s;
                            " id="authTypeOfficialLabel">
                                <input type="radio" name="authType" value="official" checked
                                    onchange="onAuthTypeChange('official')"
                                    style="margin: 0;">
                                <i class="fas fa-globe"></i> 官方版
                            </label>
                            <label style="
                                display: flex; align-items: center; gap: 6px;
                                padding: 8px 16px; border: 2px solid var(--border-color);
                                border-radius: 8px; cursor: pointer; flex: 1;
                                background: var(--card-bg);
                                transition: all 0.2s;
                            " id="authTypeEnterpriseLabel">
                                <input type="radio" name="authType" value="enterprise"
                                    onchange="onAuthTypeChange('enterprise')"
                                    style="margin: 0;">
                                <i class="fas fa-building"></i> 企业版
                            </label>
                        </div>
                    </div>

                    <!-- 企业版额外字段 -->
                    <div id="enterpriseAuthFields" class="hidden" style="margin-bottom: 1rem;">
                        <div class="form-group" style="margin-bottom: 0.5rem;">
                            <label class="form-label" for="enterpriseEndpoint">API 端点 <span style="color: var(--error-color);">*</span></label>
                            <div style="position: relative;">
                                <input type="text" id="enterpriseEndpoint" class="form-input"
                                    placeholder="https://your-enterprise.copilot.example.com"
                                    onfocus="showEnterpriseEndpointHistory()" oninput="filterEnterpriseEndpointHistory()">
                                <div id="enterpriseEndpointHistory" class="autocomplete-dropdown hidden"></div>
                            </div>
                        </div>
                        <div class="form-group">
                            <label class="form-label" for="enterpriseId">企业标识 <span style="color: var(--error-color);">*</span></label>
                            <div style="position: relative;">
                                <input type="text" id="enterpriseId" class="form-input"
                                    placeholder="如: h3c"
                                    onfocus="showEnterpriseIdHistory()" oninput="filterEnterpriseIdHistory()">
                                <div id="enterpriseIdHistory" class="autocomplete-dropdown hidden"></div>
                            </div>
                        </div>
                    </div>
```

- [ ] **Step 2: 添加 CSS 样式**

在 `<style>` 区域中添加自动补全下拉列表样式：

```css
.autocomplete-dropdown {
    position: absolute;
    top: 100%;
    left: 0;
    right: 0;
    max-height: 150px;
    overflow-y: auto;
    background: var(--card-bg);
    border: 1px solid var(--border-color);
    border-radius: 0 0 8px 8px;
    z-index: 100;
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
}
.autocomplete-dropdown .autocomplete-item {
    padding: 8px 12px;
    cursor: pointer;
    font-size: 0.85rem;
    transition: background 0.15s;
}
.autocomplete-dropdown .autocomplete-item:hover {
    background: var(--primary-alpha);
}
.autocomplete-dropdown .autocomplete-item:last-child {
    border-radius: 0 0 8px 8px;
}
```

- [ ] **Step 3: 添加 JavaScript 函数**

在 `<script>` 区域中添加以下函数：

```javascript
// === 认证类型切换 ===
function onAuthTypeChange(type) {
    const officialLabel = document.getElementById('authTypeOfficialLabel');
    const enterpriseLabel = document.getElementById('authTypeEnterpriseLabel');
    const enterpriseFields = document.getElementById('enterpriseAuthFields');

    if (type === 'enterprise') {
        officialLabel.style.borderColor = 'var(--border-color)';
        officialLabel.style.background = 'var(--card-bg)';
        enterpriseLabel.style.borderColor = 'var(--primary)';
        enterpriseLabel.style.background = 'var(--primary-alpha)';
        enterpriseFields.classList.remove('hidden');
    } else {
        officialLabel.style.borderColor = 'var(--primary)';
        officialLabel.style.background = 'var(--primary-alpha)';
        enterpriseLabel.style.borderColor = 'var(--border-color)';
        enterpriseLabel.style.background = 'var(--card-bg)';
        enterpriseFields.classList.add('hidden');
    }
}

// === 企业历史记录管理 ===
function getEnterpriseHistory(key) {
    try {
        return JSON.parse(localStorage.getItem(key) || '[]');
    } catch { return []; }
}

function saveEnterpriseHistory(key, value) {
    let history = getEnterpriseHistory(key);
    history = history.filter(item => item !== value);
    history.unshift(value);
    if (history.length > 10) history = history.slice(0, 10);
    localStorage.setItem(key, JSON.stringify(history));
}

function showAutocompleteDropdown(dropdownId, inputId, historyKey) {
    const dropdown = document.getElementById(dropdownId);
    const input = document.getElementById(inputId);
    const history = getEnterpriseHistory(historyKey);
    const filter = input.value.toLowerCase();

    if (history.length === 0) {
        dropdown.classList.add('hidden');
        return;
    }

    const filtered = filter ? history.filter(item => item.toLowerCase().includes(filter)) : history;

    if (filtered.length === 0) {
        dropdown.classList.add('hidden');
        return;
    }

    dropdown.innerHTML = filtered.map(item =>
        `<div class="autocomplete-item" onclick="selectAutocompleteItem('${inputId}', '${dropdownId}', '${item.replace(/'/g, "\\'")}')">${item}</div>`
    ).join('');
    dropdown.classList.remove('hidden');
}

function selectAutocompleteItem(inputId, dropdownId, value) {
    document.getElementById(inputId).value = value;
    document.getElementById(dropdownId).classList.add('hidden');
}

function showEnterpriseEndpointHistory() {
    showAutocompleteDropdown('enterpriseEndpointHistory', 'enterpriseEndpoint', 'codebuddy_enterprise_endpoints');
}

function filterEnterpriseEndpointHistory() {
    showEnterpriseEndpointHistory();
}

function showEnterpriseIdHistory() {
    showAutocompleteDropdown('enterpriseIdHistory', 'enterpriseId', 'codebuddy_enterprise_ids');
}

function filterEnterpriseIdHistory() {
    showEnterpriseIdHistory();
}

// 点击其他区域时关闭下拉列表
document.addEventListener('click', function(e) {
    ['enterpriseEndpointHistory', 'enterpriseIdHistory'].forEach(id => {
        const dropdown = document.getElementById(id);
        if (dropdown && !dropdown.contains(e.target)) {
            dropdown.classList.add('hidden');
        }
    });
});
```

- [ ] **Step 4: 修改 `startAuth()` 函数传递类型参数**

修改 `startAuth()` 函数（约第 2818 行），在 `fetch('/codebuddy/auth/start'` 调用中添加查询参数：

将：
```javascript
                const response = await fetch('/codebuddy/auth/start', { method: 'GET' });
```

改为：
```javascript
                const authType = document.querySelector('input[name="authType"]:checked').value;
                let authUrl = `/codebuddy/auth/start?auth_type=${authType}`;

                if (authType === 'enterprise') {
                    const enterpriseId = document.getElementById('enterpriseId').value.trim();
                    const enterpriseEndpoint = document.getElementById('enterpriseEndpoint').value.trim();
                    if (!enterpriseId) {
                        showNotification('企业版认证需要提供企业标识', 'error');
                        resetAuthButton();
                        return;
                    }
                    if (!enterpriseEndpoint) {
                        showNotification('企业版认证需要提供API端点', 'error');
                        resetAuthButton();
                        return;
                    }
                    authUrl += `&enterprise_id=${encodeURIComponent(enterpriseId)}&api_endpoint=${encodeURIComponent(enterpriseEndpoint)}`;
                }

                const response = await fetch(authUrl, { method: 'GET' });
```

- [ ] **Step 5: 修改 `pollForToken()` 传递类型参数**

找到 `pollForToken()` 函数（约第 2925 行），修改 `fetch('/codebuddy/auth/poll',` 请求体，添加 `auth_type`、`enterprise_id`、`api_endpoint`：

在请求体中添加（与 `auth_state` 一起发送）：

```javascript
                    const pollBody = {
                        auth_state: currentAuthData.auth_state,
                        auth_type: currentAuthData.auth_type || 'official',
                        enterprise_id: currentAuthData.enterprise_id || null,
                        api_endpoint: currentAuthData.api_endpoint || null
                    };
                    const response = await fetch('/codebuddy/auth/poll', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(pollBody)
                    });
```

注意：`currentAuthData` 在 `startAuth()` 成功后保存了 `auth_type`、`enterprise_id`、`api_endpoint`。

- [ ] **Step 6: 认证成功后保存企业历史记录**

在 `pollForToken()` 的认证成功分支中（找到认证成功的处理逻辑），添加保存历史记录：

```javascript
                    // 保存企业历史记录到 localStorage
                    if (currentAuthData.auth_type === 'enterprise') {
                        saveEnterpriseHistory('codebuddy_enterprise_endpoints', currentAuthData.api_endpoint);
                        saveEnterpriseHistory('codebuddy_enterprise_ids', currentAuthData.enterprise_id);
                    }
```

- [ ] **Step 7: Commit**

```bash
git add frontend/admin.html
git commit -m "feat: add auth type selector with enterprise history autocomplete"
```

---

### Task 11: 前端 - 手动添加凭证支持类型选择

**Files:**
- Modify: `frontend/admin.html`

- [ ] **Step 1: 在手动添加凭证区域添加类型选择器**

在 `<!-- 手动添加凭证区域 -->` 的 `<div class="form-group">` 之前（约第 1445 行），添加类型选择器（复用 Task 10 的样式）：

```html
                    <!-- 凭证类型选择 -->
                    <div class="form-group" style="margin-bottom: 1rem;">
                        <label class="form-label" style="margin-bottom: 0.5rem;">凭证类型</label>
                        <div style="display: flex; gap: 0.5rem;">
                            <label style="
                                display: flex; align-items: center; gap: 6px;
                                padding: 8px 16px; border: 2px solid var(--primary);
                                border-radius: 8px; cursor: pointer; flex: 1;
                                background: var(--primary-alpha);
                                transition: all 0.2s;
                            " id="manualTypeOfficialLabel">
                                <input type="radio" name="manualType" value="official" checked
                                    onchange="onManualTypeChange('official')"
                                    style="margin: 0;">
                                <i class="fas fa-globe"></i> 官方版
                            </label>
                            <label style="
                                display: flex; align-items: center; gap: 6px;
                                padding: 8px 16px; border: 2px solid var(--border-color);
                                border-radius: 8px; cursor: pointer; flex: 1;
                                background: var(--card-bg);
                                transition: all 0.2s;
                            " id="manualTypeEnterpriseLabel">
                                <input type="radio" name="manualType" value="enterprise"
                                    onchange="onManualTypeChange('enterprise')"
                                    style="margin: 0;">
                                <i class="fas fa-building"></i> 企业版
                            </label>
                        </div>
                    </div>

                    <!-- 手动添加 - 企业版额外字段 -->
                    <div id="manualEnterpriseFields" class="hidden" style="margin-bottom: 1rem;">
                        <div class="form-group" style="margin-bottom: 0.5rem;">
                            <label class="form-label" for="manualEndpoint">API 端点 <span style="color: var(--error-color);">*</span></label>
                            <div style="position: relative;">
                                <input type="text" id="manualEndpoint" class="form-input"
                                    placeholder="https://your-enterprise.copilot.example.com"
                                    onfocus="showManualEndpointHistory()" oninput="filterManualEndpointHistory()">
                                <div id="manualEndpointHistory" class="autocomplete-dropdown hidden"></div>
                            </div>
                        </div>
                        <div class="form-group">
                            <label class="form-label" for="manualEnterpriseId">企业标识 <span style="color: var(--error-color);">*</span></label>
                            <div style="position: relative;">
                                <input type="text" id="manualEnterpriseId" class="form-input"
                                    placeholder="如: h3c"
                                    onfocus="showManualEnterpriseIdHistory()" oninput="filterManualEnterpriseIdHistory()">
                                <div id="manualEnterpriseIdHistory" class="autocomplete-dropdown hidden"></div>
                            </div>
                        </div>
                    </div>
```

- [ ] **Step 2: 添加手动添加类型的 JS 函数**

```javascript
// === 手动添加类型切换 ===
function onManualTypeChange(type) {
    const officialLabel = document.getElementById('manualTypeOfficialLabel');
    const enterpriseLabel = document.getElementById('manualTypeEnterpriseLabel');
    const enterpriseFields = document.getElementById('manualEnterpriseFields');

    if (type === 'enterprise') {
        officialLabel.style.borderColor = 'var(--border-color)';
        officialLabel.style.background = 'var(--card-bg)';
        enterpriseLabel.style.borderColor = 'var(--primary)';
        enterpriseLabel.style.background = 'var(--primary-alpha)';
        enterpriseFields.classList.remove('hidden');
    } else {
        officialLabel.style.borderColor = 'var(--primary)';
        officialLabel.style.background = 'var(--primary-alpha)';
        enterpriseLabel.style.borderColor = 'var(--border-color)';
        enterpriseLabel.style.background = 'var(--card-bg)';
        enterpriseFields.classList.add('hidden');
    }
}

function showManualEndpointHistory() {
    showAutocompleteDropdown('manualEndpointHistory', 'manualEndpoint', 'codebuddy_enterprise_endpoints');
}
function filterManualEndpointHistory() { showManualEndpointHistory(); }
function showManualEnterpriseIdHistory() {
    showAutocompleteDropdown('manualEnterpriseIdHistory', 'manualEnterpriseId', 'codebuddy_enterprise_ids');
}
function filterManualEnterpriseIdHistory() { showManualEnterpriseIdHistory(); }
```

- [ ] **Step 3: 修改 `addCredential()` 函数传递类型参数**

修改 `addCredential()` 函数（约第 2487 行）：

```javascript
        async function addCredential() {
            const bearerToken = document.getElementById('bearerToken').value.trim();
            const userId = document.getElementById('userId').value.trim();
            const manualType = document.querySelector('input[name="manualType"]:checked').value;

            if (!bearerToken) {
                showNotification('请输入 Bearer Token', 'error');
                return;
            }

            let apiEndpoint = 'https://www.codebuddy.ai';
            let enterpriseId = null;

            if (manualType === 'enterprise') {
                apiEndpoint = document.getElementById('manualEndpoint').value.trim();
                enterpriseId = document.getElementById('manualEnterpriseId').value.trim();
                if (!apiEndpoint) {
                    showNotification('企业版凭证需要提供API端点', 'error');
                    return;
                }
                if (!enterpriseId) {
                    showNotification('企业版凭证需要提供企业标识', 'error');
                    return;
                }
            }

            try {
                const response = await fetch('/codebuddy/v1/credentials', {
                    method: 'POST',
                    headers: getAuthHeaders(),
                    body: JSON.stringify({
                        bearer_token: bearerToken,
                        user_id: userId || undefined,
                        api_endpoint: apiEndpoint,
                        enterprise_id: enterpriseId
                    })
                });

                if (response.ok) {
                    const data = await response.json();
                    showNotification('凭证添加成功！', 'success');
                    document.getElementById('bearerToken').value = '';
                    document.getElementById('userId').value = '';
                    if (manualType === 'enterprise') {
                        document.getElementById('manualEndpoint').value = '';
                        document.getElementById('manualEnterpriseId').value = '';
                    }
                    // 保存企业历史记录
                    if (manualType === 'enterprise') {
                        saveEnterpriseHistory('codebuddy_enterprise_endpoints', apiEndpoint);
                        saveEnterpriseHistory('codebuddy_enterprise_ids', enterpriseId);
                    }
                    loadCredentials();
                } else {
                    const errorData = await response.json();
                    showNotification(`添加失败: ${errorData.detail || '未知错误'}`, 'error');
                }
            } catch (error) {
                showNotification(`网络错误: ${error.message}`, 'error');
            }
        }
```

- [ ] **Step 4: Commit**

```bash
git add frontend/admin.html
git commit -m "feat: manual credential add supports type selection with enterprise autocomplete"
```

---

### Task 12: 前端 - 凭证卡片显示类型标签

**Files:**
- Modify: `frontend/admin.html`

- [ ] **Step 1: 在凭证卡片渲染中添加类型标签**

在 `displayCredentials()` 函数的凭证 item HTML 模板中（约第 2348 行），在 `<div class="credential-header">` 之后添加类型标签：

找到：
```html
                                    <div class="credential-header">
                                        <div class="credential-title">凭证 #${index + 1}</div>
                                        ${selectedBadge}
                                    </div>
```

替换为：
```html
                                    <div class="credential-header">
                                        <div class="credential-title">凭证 #${index + 1}
                                            <span style="
                                                display: inline-block; margin-left: 6px;
                                                padding: 1px 6px; border-radius: 4px;
                                                font-size: 0.7rem; font-weight: 600;
                                                ${cred.enterprise_id
                                                    ? 'background: rgba(255, 152, 0, 0.15); color: #ff9800;'
                                                    : 'background: rgba(76, 175, 80, 0.15); color: #4caf50;'
                                                }
                                            ">${cred.enterprise_id ? '企业: ' + cred.enterprise_id : '官方'}</span>
                                        </div>
                                        ${selectedBadge}
                                    </div>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/admin.html
git commit -m "feat: display credential type badge (official/enterprise) on credential cards"
```

---

### Task 13: 前端 - 仪表盘和设置页面清理

**Files:**
- Modify: `frontend/admin.html`

- [ ] **Step 1: 确认仪表盘 API 端点卡片无需修改**

仪表盘中的 API 端点卡片（第 1277-1315 行）显示的是本服务的 OpenAI/Anthropic 兼容端点（`/codebuddy/v1` 和 `/anthropic`），不是 CodeBuddy 上游端点，因此**无需修改**。

- [ ] **Step 2: 确认设置页面已自动清理**

由于 `settings_router.py` 的 `SETTING_LABELS` 已移除了 `CODEBUDDY_API_ENDPOINT` 和 `CODEBUDDY_ENTERPRISE_ID`，设置页面的 `loadSettings()` 会自动从后端返回的配置中渲染表单，不再显示这两个配置项。**无需修改前端设置页面代码**。

- [ ] **Step 3: Commit（如有任何修改）**

如果本步骤没有代码修改，跳过 commit。

---

### Task 14: 端到端验证

- [ ] **Step 1: 启动项目**

Run: `cd D:/code2/codebuddy2api && python web.py`
Expected: 服务正常启动，无导入错误

- [ ] **Step 2: 验证凭证迁移**

检查 `.codebuddy_creds/` 目录下的凭证 JSON 文件是否已自动添加 `api_endpoint` 和 `enterprise_id` 字段。

- [ ] **Step 3: 验证设置页面**

打开管理界面 -> 设置页面，确认不再显示 "CodeBuddy 官方API端点" 和企业 ID 配置项。

- [ ] **Step 4: 验证凭证卡片**

打开管理界面 -> 凭证管理，确认每个凭证卡片显示类型标签（"官方" 或 "企业: xxx"）。

- [ ] **Step 5: 验证认证类型选择器**

在凭证管理页面，确认自动认证区域显示官方/企业类型选择器，选择企业版时显示额外输入框。

- [ ] **Step 6: 验证手动添加凭证**

确认手动添加凭证区域也显示类型选择器，选择企业版时显示额外输入框。

- [ ] **Step 7: 验证企业历史记录**

1. 手动添加一个企业凭证（输入端点和企业 ID）
2. 刷新页面
3. 再次打开企业版输入框，确认历史记录下拉列表显示之前输入的值
