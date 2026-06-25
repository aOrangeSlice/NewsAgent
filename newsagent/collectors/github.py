from __future__ import annotations

from newsagent.http import fetch_json

from .base import Collector


class GitHubSearchCollector(Collector):
    API = "https://api.github.com/search/repositories"

    def collect(self, limit: int = 20):
        data = fetch_json(
            self.API,
            params={
                "q": self.source.extra.get("query", "topic:artificial-intelligence"),
                "sort": self.source.extra.get("sort", "updated"),
                "order": self.source.extra.get("order", "desc"),
                "per_page": min(limit, 50),
            },
            verify_ssl=bool(self.source.extra.get("verify_ssl", True)),
        )
        results = []
        for repo in data.get("items", [])[:limit]:
            metrics = {
                "stars": repo.get("stargazers_count"),
                "forks": repo.get("forks_count"),
                "open_issues": repo.get("open_issues_count"),
                "language": repo.get("language"),
                "pushed_at": repo.get("pushed_at"),
            }
            title = f"{repo.get('full_name')}: {repo.get('description') or ''}".strip()
            results.append(
                self.item(
                    title=title,
                    url=repo.get("html_url", ""),
                    summary=repo.get("description") or "",
                    published_at=repo.get("pushed_at") or repo.get("updated_at") or "",
                    metrics=metrics,
                    tags=self.source.tags + ["github"],
                )
            )
        return results
