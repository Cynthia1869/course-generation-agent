from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class PromptSpec:
    prompt_id: str
    version: str
    provider: str
    mode: str
    step_id: str | None
    purpose: str
    input_vars: tuple[str, ...]
    output_contract: str
    file: str
    system_prompt_id: str | None = None


@dataclass(frozen=True)
class PromptBundle:
    prompt_id: str
    system_prompt_id: str | None
    prompt_ids: tuple[str, ...]
    system_prompt: str | None
    user_prompt: str
    combined_prompt: str


class PromptRegistry:
    def __init__(self, prompt_root: Path) -> None:
        self.prompt_root = prompt_root
        self.catalog_path = prompt_root / "prompt_catalog.yaml"
        self._catalog = self._load_catalog()

    def _load_catalog(self) -> dict[str, PromptSpec]:
        if not self.catalog_path.exists():
            return {}
        payload = yaml.safe_load(self.catalog_path.read_text(encoding="utf-8")) or {}
        prompts = payload.get("prompts", [])
        catalog: dict[str, PromptSpec] = {}
        for item in prompts:
            prompt_id = item["prompt_id"]
            file_path = item["file"]
            if prompt_id in catalog:
                raise ValueError(f"Duplicate prompt_id in catalog: {prompt_id}")
            resolved_file = self.prompt_root / file_path
            if not resolved_file.exists():
                raise FileNotFoundError(f"Prompt file for '{prompt_id}' not found: {file_path}")
            spec = PromptSpec(
                prompt_id=prompt_id,
                version=str(item["version"]),
                provider=item["provider"],
                mode=item["mode"],
                step_id=item.get("step_id"),
                purpose=item["purpose"],
                input_vars=tuple(item.get("input_vars", [])),
                output_contract=item["output_contract"],
                file=file_path,
                system_prompt_id=item.get("system_prompt_id"),
            )
            catalog[spec.prompt_id] = spec
        return catalog

    def resolve_prompt(self, prompt_id: str) -> PromptSpec:
        if prompt_id not in self._catalog:
            raise KeyError(f"Unknown prompt_id: {prompt_id}")
        return self._catalog[prompt_id]

    def validate_inputs(self, prompt_id: str, kwargs: dict[str, Any]) -> None:
        spec = self.resolve_prompt(prompt_id)
        self._validate_spec_inputs(spec, kwargs)
        if spec.system_prompt_id:
            system_spec = self.resolve_prompt(spec.system_prompt_id)
            self._validate_spec_inputs(system_spec, kwargs)

    def _validate_spec_inputs(self, spec: PromptSpec, kwargs: dict[str, Any]) -> None:
        missing = [name for name in spec.input_vars if name not in kwargs or kwargs[name] is None]
        if missing:
            raise ValueError(f"Prompt '{spec.prompt_id}' missing required input vars: {', '.join(missing)}")

    def load_legacy(self, relative_path: str) -> str:
        return (self.prompt_root / relative_path).read_text(encoding="utf-8").strip()

    def load(self, relative_path: str) -> str:
        return self.load_legacy(relative_path)

    def exists(self, relative_path: str) -> bool:
        return (self.prompt_root / relative_path).exists()

    def load_optional(self, relative_path: str) -> str | None:
        path = self.prompt_root / relative_path
        if not path.exists():
            return None
        content = path.read_text(encoding="utf-8").strip()
        return content or None

    def render(self, relative_path: str, **kwargs: object) -> str:
        template = self.load_legacy(relative_path)
        return template.format(**kwargs)

    def render_by_id(self, prompt_id: str, **kwargs: object) -> str:
        return self.render_bundle(prompt_id, **kwargs).combined_prompt

    def render_bundle(self, prompt_id: str, **kwargs: object) -> PromptBundle:
        self.validate_inputs(prompt_id, kwargs)
        spec = self.resolve_prompt(prompt_id)
        user_prompt = self.load_legacy(spec.file).format(**kwargs)

        system_prompt: str | None = None
        prompt_ids: list[str] = [prompt_id]
        if spec.system_prompt_id:
            system_spec = self.resolve_prompt(spec.system_prompt_id)
            system_prompt = self.load_legacy(system_spec.file).format(**kwargs)
            prompt_ids.insert(0, system_spec.prompt_id)

        combined_prompt = f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt
        return PromptBundle(
            prompt_id=prompt_id,
            system_prompt_id=spec.system_prompt_id,
            prompt_ids=tuple(prompt_ids),
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            combined_prompt=combined_prompt,
        )
