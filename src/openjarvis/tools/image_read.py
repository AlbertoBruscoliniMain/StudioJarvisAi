"""Image reading tool — describe/analyze images using a local vision model."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_DEFAULT_VISION_MODEL = "moondream:latest"
_OLLAMA_HOST = "http://localhost:11434"


def _image_to_base64(path: str) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("utf-8")


@ToolRegistry.register("image_read")
class ImageReadTool(BaseTool):
    """Analyze or describe an image using a local vision model (moondream)."""

    tool_id = "image_read"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="image_read",
            description=(
                "Read and analyze an image or screenshot from a file path. "
                "Returns a text description of what is in the image. "
                "Useful for reading screenshots, diagrams, photos, or any visual content."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the image file (png, jpg, jpeg, gif, webp).",
                    },
                    "question": {
                        "type": "string",
                        "description": (
                            "Optional specific question about the image. "
                            "Default: 'Describe this image in detail.'"
                        ),
                    },
                    "model": {
                        "type": "string",
                        "description": f"Vision model to use. Default: {_DEFAULT_VISION_MODEL}",
                    },
                },
                "required": ["path"],
            },
            category="media",
        )

    def execute(self, **params: Any) -> ToolResult:
        path = params.get("path", "").strip()
        question = params.get("question", "Describe this image in detail.")
        model = params.get("model", _DEFAULT_VISION_MODEL)

        if not path:
            return ToolResult(tool_name="image_read", content="No path provided.", success=False)

        img_path = Path(path).expanduser()
        if not img_path.exists():
            return ToolResult(tool_name="image_read", content=f"File not found: {path}", success=False)

        suffix = img_path.suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
            return ToolResult(
                tool_name="image_read",
                content=f"Unsupported format '{suffix}'. Use: png, jpg, jpeg, gif, webp.",
                success=False,
            )

        try:
            b64 = _image_to_base64(str(img_path))
        except Exception as exc:
            return ToolResult(tool_name="image_read", content=f"Failed to read image: {exc}", success=False)

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": question,
                    "images": [b64],
                }
            ],
            "stream": False,
        }

        try:
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(f"{_OLLAMA_HOST}/api/chat", json=payload)
                resp.raise_for_status()
        except httpx.ConnectError:
            return ToolResult(
                tool_name="image_read",
                content="Ollama not reachable. Make sure it is running.",
                success=False,
            )
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                tool_name="image_read",
                content=f"Ollama error {exc.response.status_code}: {exc.response.text[:300]}",
                success=False,
            )

        data = resp.json()
        description = data.get("message", {}).get("content", "").strip()

        return ToolResult(
            tool_name="image_read",
            content=description,
            success=True,
            metadata={"model": model, "image_path": str(img_path)},
        )


__all__ = ["ImageReadTool"]
