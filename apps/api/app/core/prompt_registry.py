from __future__ import annotations

from pathlib import Path


class PromptRegistry:
    def __init__(self, prompt_root: Path) -> None:
        self.prompt_root = prompt_root

    def load(self, relative_path: str) -> str:
        return (self.prompt_root / relative_path).read_text(encoding="utf-8").strip()

    def render(self, relative_path: str, **kwargs: object) -> str:
        template = self.load(relative_path)
        return template.format(**kwargs)
