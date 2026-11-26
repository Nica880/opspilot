"""Check pinned image tags exist for linux/arm64 (like `docker manifest inspect`, no daemon)."""

from __future__ import annotations

import json
import sys
import urllib.request
from typing import Any

ACCEPT = ",".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)


def _get(url: str, headers: dict[str, str] | None = None) -> tuple[Any, dict[str, str]]:
    req = urllib.request.Request(url, headers=headers or {})  # noqa: S310 - fixed https hosts
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - fixed registry hosts
        return json.loads(resp.read()), dict(resp.headers)


def platforms(image: str) -> list[str]:
    name, tag = image.rsplit(":", 1)
    registry = "registry-1.docker.io"
    if name.startswith("ghcr.io/"):
        registry, name = "ghcr.io", name.removeprefix("ghcr.io/")
        token = _get(f"https://ghcr.io/token?scope=repository:{name}:pull")[0]["token"]
    elif name.startswith("quay.io/"):
        registry, name, token = "quay.io", name.removeprefix("quay.io/"), ""
    else:
        if "/" not in name:
            name = f"library/{name}"
        token = _get(
            f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{name}:pull"
        )[0]["token"]
    headers = {"Accept": ACCEPT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    manifest, _ = _get(f"https://{registry}/v2/{name}/manifests/{tag}", headers)
    if "manifests" in manifest:
        return sorted(
            f"{m['platform']['os']}/{m['platform']['architecture']}"
            for m in manifest["manifests"]
            if m.get("platform", {}).get("os") != "unknown"
        )
    return ["single-arch manifest (check config)"]


def main(images: list[str]) -> int:
    failed = 0
    for image in images:
        try:
            plats = platforms(image)
        except Exception as exc:
            print(f"MISSING  {image}: {exc}")
            failed += 1
            continue
        ok = "linux/arm64" in plats
        failed += 0 if ok else 1
        print(f"{'OK     ' if ok else 'NO-ARM '}  {image}: {', '.join(plats)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
