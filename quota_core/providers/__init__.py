"""Provider registry. Order here is the card order in the panel."""
from .claude import ClaudeProvider
from .codex import CodexProvider
from .grok import GrokProvider

PROVIDER_CLASSES = [ClaudeProvider, CodexProvider, GrokProvider]


def build_providers(cfg: dict) -> list:
    out = []
    for cls in PROVIDER_CLASSES:
        pcfg = (cfg.get("providers") or {}).get(cls.key, {})
        if pcfg.get("enabled", True):
            out.append(cls(pcfg))
    return out
