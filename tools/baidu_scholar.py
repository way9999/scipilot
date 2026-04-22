"""百度学术搜索后端

通过 Playwright（有头浏览器）搜索百度学术。
需要先手动通过验证码获取 cookie，之后可复用。

注意：headless 模式会被百度检测并触发验证码。
建议使用 cookie 文件方式绕过验证。
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

_COOKIE_FILE = Path(__file__).resolve().parents[1] / "knowledge-base" / "baidu_scholar_cookies.json"


def _load_cookies() -> list[dict]:
    if _COOKIE_FILE.exists():
        try:
            return json.loads(_COOKIE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def search_papers(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search Baidu Scholar using Playwright with saved cookies.

    Returns empty list if cookies are not available or search fails.
    """
    cookies = _load_cookies()
    if not cookies:
        return []

    try:
        from playwright.sync_api import sync_playwright

        results: list[dict[str, Any]] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                locale="zh-CN",
            )
            context.add_cookies(cookies)
            page = context.new_page()

            page.goto(
                f"https://xueshu.baidu.com/s?wd={query}&ie=utf-8",
                timeout=15000,
            )
            time.sleep(2)

            content = page.content()
            if "验证" in content or "captcha" in content.lower():
                browser.close()
                return []

            items = page.query_selector_all(".result")
            for item in items[:limit]:
                try:
                    title_el = item.query_selector(".t a")
                    if not title_el:
                        continue
                    title = title_el.inner_text().strip()
                    url = title_el.get_attribute("href") or ""

                    info_el = item.query_selector(".sc_info")
                    info_text = info_el.inner_text().strip() if info_el else ""

                    authors: list[str] = []
                    year: int | None = None
                    venue = ""
                    if info_text:
                        parts = [s.strip() for s in info_text.split("-")]
                        if parts:
                            authors = [a.strip() for a in parts[0].split(" ") if a.strip()]
                        for part in parts:
                            ym = re.search(r"(20\d{2}|19\d{2})", part)
                            if ym and not year:
                                year = int(ym.group(1))
                            elif not venue and len(part) > 2:
                                venue = part

                    abs_el = item.query_selector(".c_abstract")
                    abstract = abs_el.inner_text().strip()[:2000] if abs_el else ""

                    results.append({
                        "title": title,
                        "authors": authors,
                        "year": year,
                        "doi": "",
                        "url": url,
                        "abstract": abstract,
                        "venue": venue,
                        "citation_count": 0,
                        "source": "baidu_scholar",
                    })
                except Exception:
                    continue

            browser.close()
        return results[:limit]
    except Exception:
        return []
