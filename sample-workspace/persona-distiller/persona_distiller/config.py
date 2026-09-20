"""配置：工作区、存储路径与 LLM 接入参数。

配置来源优先级（高 → 低）：
1. 命令行参数
2. 环境变量  PD_API_KEY / PD_BASE_URL / PD_MODEL / PD_TIMEOUT
   以及常见厂商变量  DASHSCOPE_API_KEY（阿里云百炼/Qwen）、OPENAI_API_KEY、DEEPSEEK_API_KEY
3. ``<root>/.persona-distiller/config.json``
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from .util import read_json, tool_dir

CONFIG_FILE_NAME = "config.json"

_DEFAULT_ENTRIES = (
    # (环境变量名, base_url, 默认模型)
    ("DASHSCOPE_API_KEY", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    ("OPENAI_API_KEY", "https://api.openai.com/v1", "gpt-4o-mini"),
    ("DEEPSEEK_API_KEY", "https://api.deepseek.com/v1", "deepseek-chat"),
    ("MOONSHOT_API_KEY", "https://api.moonshot.cn/v1", "moonshot-v1-8k"),
    ("PD_API_KEY", os.environ.get("PD_BASE_URL") or "", ""),
)


@dataclass
class LLMConfig:
    """OpenAI 兼容接口的最小配置。"""

    api_key: str = ""
    base_url: str = ""
    model: str = ""
    timeout: int = 120
    temperature: float = 0.65
    source: str = ""

    def available(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def describe(self) -> str:
        if not self.available():
            return "未配置可用 LLM（离线检索模式）"
        return "%s @ %s（来源：%s）" % (self.model, self.base_url, self.source or "unknown")


@dataclass
class Config:
    root: Path
    llm: LLMConfig = field(default_factory=LLMConfig)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def dir(self) -> Path:
        return tool_dir(self.root)

    def save(self) -> Path:
        from .util import write_json

        path = self.dir / CONFIG_FILE_NAME
        write_json(path, self.raw)
        return path


def load_config(root: Path, overrides: Optional[Dict[str, Any]] = None) -> Config:
    """合并文件配置、环境变量与显式覆盖，返回 :class:`Config`。"""
    root = Path(root)
    file_cfg: Dict[str, Any] = read_json(tool_dir(root) / CONFIG_FILE_NAME, {}) or {}
    llm_file = (file_cfg.get("llm") or {}) if isinstance(file_cfg, dict) else {}

    api_key = os.environ.get("PD_API_KEY") or llm_file.get("api_key") or ""
    base_url = os.environ.get("PD_BASE_URL") or llm_file.get("base_url") or ""
    model = os.environ.get("PD_MODEL") or llm_file.get("model") or ""
    source = "config.json" if llm_file else ""

    if not api_key:
        for env_name, env_base, env_model in _DEFAULT_ENTRIES:
            key = os.environ.get(env_name)
            if key:
                api_key = key
                base_url = base_url or env_base
                model = model or env_model
                source = env_name
                break

    timeout = int(os.environ.get("PD_TIMEOUT") or llm_file.get("timeout") or 120)
    temperature = float(llm_file.get("temperature", 0.65))

    cfg = Config(
        root=root,
        llm=LLMConfig(
            api_key=api_key, base_url=base_url.rstrip("/"), model=model,
            timeout=timeout, temperature=temperature, source=source,
        ),
        raw=file_cfg if isinstance(file_cfg, dict) else {},
    )

    for key, value in (overrides or {}).items():
        if value is None:
            continue
        setattr(cfg.llm, key, value)
    return cfg
