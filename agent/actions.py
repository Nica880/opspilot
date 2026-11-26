"""The only code path that writes to GitHub. Every call passes `check_action` (sandbox only)."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from agent.guardrails import check_action, redact
from ingestion.github import API_URL

MIRROR_MARKER = "<!-- opspilot-mirror:{key} -->"


@dataclass(frozen=True)
class AppliedAction:
    issue_url: str
    comment_url: str | None
    labels: list[str]


class SandboxActions:
    def __init__(
        self,
        token: str,
        sandbox_repo: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.sandbox_repo = sandbox_repo
        self._http = httpx.AsyncClient(
            base_url=API_URL,
            transport=transport,
            timeout=30.0,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "opspilot-sandbox",
            },
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _post(
        self, action: str, repo: str, path: str, payload: dict[str, object]
    ) -> dict[str, object]:
        check_action(action, repo, self.sandbox_repo)
        response = await self._http.post(f"/repos/{repo}{path}", json=payload)
        response.raise_for_status()
        data: dict[str, object] = response.json()
        return data

    async def apply(
        self,
        *,
        upstream_repo: str,
        upstream_number: int,
        upstream_title: str,
        upstream_url: str,
        reply_markdown: str,
        labels: list[str],
    ) -> AppliedAction:
        """Mirror the upstream issue into the sandbox (link only), then comment and label there."""
        key = f"{upstream_repo}#{upstream_number}"
        issue = await self._post(
            "create_mirror_issue",
            self.sandbox_repo,
            "/issues",
            {
                "title": f"[mirror] {key}: {upstream_title}"[:250],
                "body": (
                    f"Mirror of {upstream_url} for OpsPilot review. "
                    "Nothing is posted upstream.\n\n" + MIRROR_MARKER.format(key=key)
                ),
            },
        )
        number = int(str(issue["number"]))
        reply, _ = redact(reply_markdown)
        comment = await self._post(
            "comment", self.sandbox_repo, f"/issues/{number}/comments", {"body": reply}
        )
        if labels:
            await self._post(
                "add_labels", self.sandbox_repo, f"/issues/{number}/labels", {"labels": labels}
            )
        return AppliedAction(
            issue_url=str(issue["html_url"]),
            comment_url=str(comment.get("html_url")) if comment.get("html_url") else None,
            labels=labels,
        )
