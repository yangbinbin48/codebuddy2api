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

        if enterprise_id:
            headers["X-Enterprise-Id"] = enterprise_id
            headers["X-Tenant-Id"] = enterprise_id
        user_id = credential_data.get('user_id')
        if user_id:
            headers["X-User-Id"] = user_id
        domain = credential_data.get('domain')
        if domain:
            headers["X-Domain"] = domain

        try:
            client = await get_http_client()
            response = await client.post(url, json=request_body, headers=headers, timeout=30.0)

            if response.status_code != 200:
                logger.debug(f"[CreditManager] Query failed for #{index}: HTTP {response.status_code} (enterprise may not support credit API)")
                return None

            data = response.json()
            if data.get("code") != 0:
                logger.debug(f"[CreditManager] API error for #{index}: {data.get('msg')}")
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
