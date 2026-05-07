"""扩展的解析器：CSS / XPath / JSONPath。

- CSS 走 BeautifulSoup（兼容主项目）
- XPath 走 parsel（lxml 内核）
- JSONPath 走简单 dot-path（项目原本约定），可选 jsonpath-ng 增强
"""

from __future__ import annotations

import json
from typing import Any

from bs4 import BeautifulSoup

try:
    from parsel import Selector  # type: ignore
    HAS_PARSEL = True
except ImportError:
    Selector = None  # type: ignore
    HAS_PARSEL = False

try:
    from jsonpath_ng.ext import parse as jsonpath_parse  # type: ignore
    HAS_JSONPATH_NG = True
except ImportError:
    jsonpath_parse = None  # type: ignore
    HAS_JSONPATH_NG = False


SUPPORTED_TYPES = ("css", "xpath", "jsonpath")


def parse_css(html: str, selector: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    return [elem.get_text(strip=True) for elem in soup.select(selector)]


def parse_css_attr(html: str, selector: str, attr: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    return [elem.get(attr, "") for elem in soup.select(selector)]


def parse_xpath(html: str, expression: str) -> list[str]:
    if not HAS_PARSEL:
        raise RuntimeError("parsel 未安装；请安装 parsel 启用 XPath 解析")
    sel = Selector(text=html)
    nodes = sel.xpath(expression)
    results: list[str] = []
    for node in nodes:
        text = node.get()
        if text is None:
            continue
        # 优先取 text() 显式节点；如果是 element，取拼接文本
        if expression.endswith("/text()") or "::text" in expression:
            results.append(text.strip())
        else:
            try:
                joined = "".join(node.xpath(".//text()").getall()).strip()
                results.append(joined or text.strip())
            except Exception:
                results.append(text.strip())
    return results


def parse_jsonpath(payload: Any, expression: str) -> list:
    """JSONPath 解析。

    优先使用 jsonpath-ng（标准 RFC 9535 子集）；
    退化到点号路径（"data.items.0.title"）兼容主项目原行为。
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 解析失败: {exc}") from exc

    if HAS_JSONPATH_NG and (expression.startswith("$") or "[" in expression
                              or "*" in expression or ".." in expression):
        expr = jsonpath_parse(expression)
        return [match.value for match in expr.find(payload)]

    # 退化路径
    current = payload
    if not expression:
        return [current]
    for key in expression.split("."):
        if current is None:
            return []
        if isinstance(current, list):
            try:
                current = current[int(key)]
            except (ValueError, IndexError):
                return []
        elif isinstance(current, dict):
            current = current.get(key)
        else:
            return []
    if current is None:
        return []
    return current if isinstance(current, list) else [current]


def parse_with_type(html_or_payload: Any, selector: str, selector_type: str = "css",
                    attr: str = "") -> list:
    """统一入口。返回值都是 list 形式。

    - css: html + 选择器，可选 attr（取属性而不是文本）
    - xpath: html + xpath 表达式
    - jsonpath: payload (str/dict/list) + jsonpath 表达式
    """
    selector_type = (selector_type or "css").lower()
    if selector_type == "css":
        if attr:
            return parse_css_attr(str(html_or_payload), selector, attr)
        return parse_css(str(html_or_payload), selector)
    if selector_type == "xpath":
        return parse_xpath(str(html_or_payload), selector)
    if selector_type == "jsonpath":
        return parse_jsonpath(html_or_payload, selector)
    raise ValueError(f"selector_type 只支持 {SUPPORTED_TYPES}，得到: {selector_type}")
