# -*- coding: utf-8 -*-
"""查询 GitHub Releases 的最新版本。

设计约束：

- 只读取公开的 releases/latest 接口，不携带设备标识、不上报任何统计；
- 网络失败、限流、JSON 异常一律降级为 ``status="error"``，由调用方决定是否提示，
  绝不影响歌词生成主流程；
- 不做自动下载和自我替换，只返回发布页地址，交给浏览器。

返回值统一为字典：``{"status": "update" | "latest" | "error", ...}``。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from version import __version__

REPOSITORY = "ZeroOneLab/pebby-lyrics"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases/latest"
API_LATEST = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
TIMEOUT = 6.0


def parse_version(text: str) -> tuple[int, ...]:
    """把 ``v1.2.3`` / ``1.2.3-rc1`` 解析成 ``(1, 2, 3)``；无法解析时返回空元组。"""
    cleaned = (text or "").strip().lstrip("vV")
    parts: list[int] = []
    for piece in cleaned.split("."):
        digits = ""
        for char in piece:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        parts.append(int(digits))
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def is_newer(latest: str, current: str = __version__) -> bool:
    """``latest`` 是否比 ``current`` 新；任一方无法解析时判为不更新。"""
    new, cur = parse_version(latest), parse_version(current)
    if not new or not cur:
        return False
    width = max(len(new), len(cur))
    return new + (0,) * (width - len(new)) > cur + (0,) * (width - len(cur))


def fetch_latest(timeout: float = TIMEOUT) -> dict:
    """请求最新 Release，成功返回 ``{tag, version, url, notes, published_at}``。

    失败抛 ``RuntimeError``，消息直接面向用户，不含内部堆栈。
    """
    request = urllib.request.Request(
        API_LATEST,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"Pebby-Lyrics/{__version__}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError("还没有发布任何版本") from exc
        if exc.code in (403, 429):
            raise RuntimeError("GitHub 接口限流，请稍后再试") from exc
        raise RuntimeError(f"GitHub 返回 HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"网络不可用（{exc}）") from exc
    except ValueError as exc:
        raise RuntimeError("返回内容不是有效 JSON") from exc

    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        raise RuntimeError("Release 缺少 tag_name")
    return {
        "tag": tag,
        "version": tag.lstrip("vV"),
        "url": str(payload.get("html_url") or RELEASES_PAGE),
        "notes": str(payload.get("body") or ""),
        "published_at": str(payload.get("published_at") or ""),
    }


def check(current: str = __version__, timeout: float = TIMEOUT) -> dict:
    """检查更新，从不抛异常。

    返回 ``{"status": "update", ...}``、``{"status": "latest", ...}``
    或 ``{"status": "error", "message": ...}``。
    """
    try:
        latest = fetch_latest(timeout)
    except RuntimeError as exc:
        return {"status": "error", "message": str(exc)}
    if is_newer(latest["version"], current):
        return {"status": "update", **latest}
    return {
        "status": "latest",
        "version": latest["version"],
        "url": latest["url"],
    }
