"""反爬挑战页探测启发式。

支持 Cloudflare / Akamai / hCaptcha / reCAPTCHA / 极验 / 自定义关键词。
返回命中的特征字符串；未命中返回空串。
"""

from __future__ import annotations


DEFAULT_PATTERNS: list[str] = [
    "cf-challenge",
    "cf-browser-verification",
    "cf-mitigated",
    "cf-chl-bypass",
    "checking your browser",
    "just a moment",
    "attention required",
    "captcha",
    "hcaptcha",
    "recaptcha",
    "geetest",
    "_incapsula_resource",
    "请完成安全验证",
    "人机验证",
    "验证码",
    "安全验证",
]

# Header 与 cookie 的强信号（不区分大小写）
STRONG_HEADER_KEYS = ("cf-mitigated", "cf-chl-bypass", "x-amz-cf-id", "akamai-grn")


def detect_in_html(html: str, patterns: list[str] | None = None,
                   sample_size: int = 200_000) -> str:
    """在 HTML 正文里探测挑战页特征。返回命中关键词；未命中返回空串。"""
    if not html:
        return ""
    sample = html[:sample_size].lower()
    for pattern in (patterns or DEFAULT_PATTERNS):
        if pattern.lower() in sample:
            return pattern
    return ""


def detect_in_response(status_code: int, headers: dict | None,
                        cookies: dict | None, html: str = "",
                        patterns: list[str] | None = None) -> dict:
    """综合状态码 + headers + cookies + html 多重信号探测。

    返回 {"challenge": str, "confidence": "high"|"medium"|"low"|"none", "reasons": [...]}
    """
    reasons: list[str] = []
    confidence = "none"
    challenge = ""

    if status_code == 503 or status_code == 429:
        reasons.append(f"status={status_code}")
        confidence = "low"

    if headers:
        lowered = {str(k).lower(): str(v) for k, v in headers.items()}
        for key in STRONG_HEADER_KEYS:
            if key in lowered:
                reasons.append(f"header:{key}")
                confidence = "high"
                challenge = challenge or key
        if "cf-ray" in lowered and status_code in (403, 503, 429):
            reasons.append("header:cf-ray+block_status")
            confidence = "high"
            challenge = challenge or "cf-ray"

    if cookies:
        cookie_lower = {str(k).lower(): str(v) for k, v in cookies.items()}
        for cookie_key in ("__cf_bm", "cf_clearance", "_abck"):
            if cookie_key in cookie_lower:
                reasons.append(f"cookie:{cookie_key}")
                if cookie_key == "_abck":
                    confidence = "high"
                    challenge = challenge or "akamai_abck"

    matched = detect_in_html(html, patterns)
    if matched:
        reasons.append(f"body:{matched}")
        challenge = challenge or matched
        confidence = "high" if confidence == "none" else confidence

    if not challenge and confidence == "none":
        return {"challenge": "", "confidence": "none", "reasons": []}

    if not challenge:
        challenge = reasons[0] if reasons else "unknown"

    return {"challenge": challenge, "confidence": confidence, "reasons": reasons}


def is_challenge_status(status_code: int) -> bool:
    """status code 是否需要触发模式升级。"""
    return status_code in (403, 406, 429, 503, 521, 522, 523, 525)
