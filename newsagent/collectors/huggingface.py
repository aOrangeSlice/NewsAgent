from __future__ import annotations

from newsagent.http import fetch_json

from .base import Collector


class HuggingFaceModelsCollector(Collector):
    API = "https://huggingface.co/api/models"

    def collect(self, limit: int = 20):
        requested = int(self.source.extra.get("limit", limit))
        data = fetch_json(
            self.API,
            params={
                "sort": self.source.extra.get("sort", "downloads"),
                "direction": "-1",
                "limit": min(requested, limit, 50),
            },
            verify_ssl=bool(self.source.extra.get("verify_ssl", True)),
        )
        results = []
        for model in data[:limit]:
            model_id = model.get("modelId") or model.get("id") or ""
            tags = list(model.get("tags") or [])
            metrics = {
                "downloads": model.get("downloads"),
                "likes": model.get("likes"),
                "pipeline_tag": model.get("pipeline_tag"),
            }
            summary = ", ".join(tag for tag in tags[:8])
            results.append(
                self.item(
                    title=f"Hugging Face model: {model_id}",
                    url=f"https://huggingface.co/{model_id}",
                    summary=summary,
                    published_at=model.get("lastModified") or "",
                    metrics=metrics,
                    tags=self.source.tags + tags[:5],
                )
            )
        return results
