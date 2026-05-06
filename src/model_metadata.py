"""
模型元数据管理器

负责从 CodeBuddy API 获取模型详细信息（上下文大小、tokens限制等）
并提供缓存访问接口。
"""
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
import time

logger = logging.getLogger(__name__)


@dataclass
class ModelInfo:
    """单个模型的元数据"""
    id: str
    name: str
    max_input_tokens: int = 200000
    max_output_tokens: int = 4096
    supports_images: bool = False
    supports_tool_call: bool = True
    vendor: str = ""

    @property
    def context_window(self) -> int:
        return self.max_input_tokens

    @property
    def max_tokens(self) -> int:
        return self.max_output_tokens


class ModelMetadataCache:
    """模型元数据缓存"""

    def __init__(self):
        self._models: Dict[str, ModelInfo] = {}
        self._initialized = False
        self._init_failed = False

    def is_empty(self) -> bool:
        return len(self._models) == 0

    def is_initialized(self) -> bool:
        return self._initialized

    def get_model(self, model_id: str) -> Optional[ModelInfo]:
        return self._models.get(model_id)

    def get_all_models(self) -> Dict[str, ModelInfo]:
        return self._models.copy()

    def add_models(self, models: List[ModelInfo]) -> None:
        for model in models:
            if model.id not in self._models:
                self._models[model.id] = model

    def get_enhanced_model_list(self, model_ids: List[str]) -> List[Dict[str, Any]]:
        """
        获取增强的模型列表，用于 /v1/models 端点返回

        Args:
            model_ids: 配置中的模型ID列表

        Returns:
            增强的模型信息列表
        """
        result = []
        for model_id in model_ids:
            model_info = self._models.get(model_id)
            if model_info:
                result.append({
                    "id": model_id,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "codebuddy",
                    "context_window": model_info.context_window,
                    "max_tokens": model_info.max_tokens,
                })
            else:
                # 使用默认值
                result.append({
                    "id": model_id,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "codebuddy",
                    "context_window": _get_default_context_window(),
                    "max_tokens": _get_default_max_tokens(),
                })
        return result


# 全局缓存实例
_model_cache = ModelMetadataCache()


def get_model_cache() -> ModelMetadataCache:
    return _model_cache


def _get_default_context_window() -> int:
    from config import get_default_context_window
    return get_default_context_window()


def _get_default_max_tokens() -> int:
    from config import get_default_max_tokens
    return get_default_max_tokens()


def build_config_headers(credential: dict) -> dict:
    """
    根据凭证类型构建 /v3/config 请求头

    Args:
        credential: 凭证信息字典

    Returns:
        请求头字典
    """
    headers = {
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    }

    site_type = credential.get("site_type", "china")
    user_info = credential.get("user_info", {})

    if site_type == "enterprise":
        # 企业版请求头
        user_agent = credential.get("user_agent") or "VSCode/1.115.0 H3CAICODE/4.2.22590715"
        headers.update({
            "X-User-Id": user_info.get("sub", ""),
            "X-Enterprise-Id": credential.get("enterprise_id", ""),
            "X-Tenant-Id": credential.get("enterprise_id", ""),
            "X-Domain": credential.get("domain", ""),
            "X-Product": "Cloud-Hosted",
            "User-Agent": user_agent,
        })
    else:
        # 个人版请求头
        user_agent = credential.get("user_agent") or "VSCode/1.115.0 CodeBuddy/4.3.20019762"
        headers.update({
            "X-User-Id": user_info.get("sub", ""),
            "X-Domain": credential.get("domain") or "www.codebuddy.cn",
            "X-Product": "SaaS",
            "User-Agent": user_agent,
        })

    return headers


async def fetch_model_config(credential: dict) -> List[ModelInfo]:
    """
    从 CodeBuddy API 获取模型配置

    Args:
        credential: 凭证信息（CodeBuddyTokenManager 格式，包含 data 键）

    Returns:
        模型信息列表
    """
    import httpx

    # CodeBuddyTokenManager 的凭证格式: {'file_path': ..., 'data': {...}}
    data = credential.get('data', {})
    if not data:
        logger.debug("凭证数据为空，跳过获取模型配置")
        return []

    # 检查 token 是否有效
    bearer_token = data.get('bearer_token', '')
    if not bearer_token:
        logger.debug(f"凭证 {data.get('user_id', 'unknown')} 没有 token，跳过获取模型配置")
        return []

    # 构建配置端点URL
    # 注意：/v3/config 端点的域名可能与 api_endpoint 不同
    site_type = data.get("site_type", "china")
    if site_type == "enterprise":
        # 企业版使用 api_endpoint 的域名
        api_endpoint = data.get("api_endpoint", "https://h3c.copilot.qq.com")
        config_url = f"{api_endpoint}/v3/config"
    else:
        # 个人版和国际版使用固定的域名
        if site_type == "international":
            config_url = "https://api.codebuddy.ai/v3/config"
        else:
            config_url = "https://copilot.tencent.com/v3/config"

    headers = build_config_headers(data)
    headers["Authorization"] = f"Bearer {bearer_token}"

    try:
        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            response = await client.get(config_url, headers=headers)
            response.raise_for_status()
            resp_data = response.json()

            if resp_data.get("code") != 0:
                logger.warning(f"获取模型配置失败: {resp_data.get('msg', 'Unknown error')}")
                return []

            models_data = resp_data.get("data", {}).get("models")
            if not models_data:
                logger.info("API 返回的 models 为 null 或空")
                return []

            # 解析模型信息
            models = []
            for m in models_data:
                model_info = ModelInfo(
                    id=m.get("id", ""),
                    name=m.get("name", ""),
                    max_input_tokens=m.get("maxInputTokens", _get_default_context_window()),
                    max_output_tokens=m.get("maxOutputTokens", _get_default_max_tokens()),
                    supports_images=m.get("supportsImages", False),
                    supports_tool_call=m.get("supportsToolCall", True),
                    vendor=m.get("vendor", ""),
                )
                models.append(model_info)

            logger.info(f"从 {config_url} 成功获取 {len(models)} 个模型信息")
            return models

    except httpx.HTTPStatusError as e:
        logger.warning(f"HTTP 错误获取模型配置: {e}")
    except httpx.RequestError as e:
        logger.warning(f"网络错误获取模型配置: {e}")
    except Exception as e:
        logger.error(f"获取模型配置时发生意外错误: {e}")

    return []


async def initialize_model_metadata(credentials: List[dict]) -> None:
    """
    初始化模型元数据缓存

    遍历所有凭证，尝试获取模型信息。只要有一个成功，
    缓存就会被填充。

    Args:
        credentials: 凭证列表
    """
    global _model_cache

    logger.info("开始初始化模型元数据缓存...")

    success_count = 0
    for cred in credentials:
        try:
            models = await fetch_model_config(cred)
            if models:
                _model_cache.add_models(models)
                success_count += 1
        except Exception as e:
            logger.warning(f"处理凭证 {cred.get('user_id', 'unknown')} 时出错: {e}")

    if success_count > 0:
        _model_cache._initialized = True
        logger.info(f"模型元数据缓存初始化完成，共加载 {len(_model_cache._models)} 个模型")
    else:
        _model_cache._init_failed = True
        logger.warning("所有凭证都无法获取模型信息，将使用默认值")
