"""最小 LLM 客户端：OpenAI 兼容的 /chat/completions（仅标准库 urllib）。

不配置 API Key 也能用整个工具——此时蒸馏与问答走「离线检索模式」。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Sequence

from .config import LLMConfig


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, cfg: LLMConfig) -> None:
        self.cfg = cfg

    @property
    def available(self) -> bool:
        return self.cfg.available()

    def _endpoint(self) -> str:
        base = (self.cfg.base_url or "").rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    def chat(
        self,
        messages: Sequence[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: int = 2000,
        json_mode: bool = False,
    ) -> str:
        if not self.available:
            raise LLMError("未配置 LLM（缺少 api_key / base_url / model）")
        payload: Dict[str, Any] = {
            "model": self.cfg.model,
            "messages": list(messages),
            "temperature": self.cfg.temperature if temperature is None else temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            self._endpoint(),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.cfg.api_key,
                "User-Agent": "persona-distiller/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:  # noqa: S310
                body = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:500]
            except Exception:  # noqa: BLE001
                pass
            raise LLMError("LLM 调用失败 HTTP %s：%s" % (exc.code, detail))
        except urllib.error.URLError as exc:
            raise LLMError("LLM 网络不可达：%s（%s）" % (self._endpoint(), exc.reason))
        except TimeoutError:
            raise LLMError("LLM 调用超时（%ss）：%s" % (self.cfg.timeout, self._endpoint()))

        try:
            data = json.loads(body)
            return (data["choices"][0]["message"]["content"] or "").strip()
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("LLM 响应无法解析：%s（原文前 300 字：%s）" % (exc, body[:300]))

    def chat_json(self, messages: Sequence[Dict[str, str]], max_tokens: int = 3000) -> Dict[str, Any]:
        text = self.chat(messages, max_tokens=max_tokens, json_mode=True)
        return parse_json_block(text)


def parse_json_block(text: str) -> Dict[str, Any]:
    """从模型输出里稳健地取出第一个 JSON 对象。"""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start < 0:
        raise LLMError("模型未返回 JSON 对象")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                snippet = text[start: i + 1]
                try:
                    data = json.loads(snippet)
                    if isinstance(data, dict):
                        return data
                except json.JSONDecodeError as exc:
                    raise LLMError("JSON 解析失败：%s" % exc)
                break
    raise LLMError("模型输出中未找到完整 JSON")


def pack_evidence(hits: Sequence[Any], max_chars: int = 6000) -> str:
    """把检索命中打包成 ``<evidence>`` 块（带编号，便于模型标注引用）。"""
    lines: List[str] = []
    used = 0
    for i, hit in enumerate(hits, 1):
        ch = getattr(hit, "chunk", hit)
        text = re.sub(r"\s+", " ", (getattr(ch, "text", "") or "")).strip()
        if not text:
            continue
        entry = "[%d] %s\n%s" % (i, getattr(ch, "locator", "未命名来源"), text[:900])
        used += len(entry)
        lines.append(entry)
        if used > max_chars:
            break
    return "\n\n".join(lines)
