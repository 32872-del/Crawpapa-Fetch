import time
from typing import Dict, Any

class TokenMonitor:
    """监控 MiMo API 的 token 消耗"""

    def __init__(self):
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_cost = 0
        self.call_count = 0

    def log_usage(self, response: Dict[str, Any]):
        """记录一次 API 调用的 token 消耗"""
        usage = response.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        self.total_prompt_tokens += prompt_tokens
        self.total_completion_tokens += completion_tokens
        self.call_count += 1

        # MiMo 计费参考（以官方为准）
        # Pro: 输入 ~0.5元/1M tokens, 输出 ~2元/1M tokens
        cost = (prompt_tokens * 0.5 + completion_tokens * 2) / 1_000_000
        self.total_cost += cost

        print(f"[Token监控] 调用 #{self.call_count}")
        print(f"  本次: prompt={prompt_tokens}, completion={completion_tokens}")
        print(f"  累计: prompt={self.total_prompt_tokens}, completion={self.total_completion_tokens}")
        print(f"  预估费用: ¥{self.total_cost:.4f}")
        print(f"  剩余 token (7亿): {700_000_000 - self.total_prompt_tokens - self.total_completion_tokens}")

    def log_step(self, step_output):
        """CrewAI step_callback 兼容接口"""
        if hasattr(step_output, "token_usage"):
            usage = step_output.token_usage
            self.log_usage({"usage": usage})
        elif isinstance(step_output, dict) and "usage" in step_output:
            self.log_usage(step_output)

    def get_summary(self) -> str:
        return f"""
总调用次数: {self.call_count}
总 prompt tokens: {self.total_prompt_tokens}
总 completion tokens: {self.total_completion_tokens}
总 token 消耗: {self.total_prompt_tokens + self.total_completion_tokens}
预估费用: ¥{self.total_cost:.4f}
剩余 token: {700_000_000 - self.total_prompt_tokens - self.total_completion_tokens}
"""
