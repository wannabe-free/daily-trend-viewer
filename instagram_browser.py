"""Optional public-page rendering fallback for the scheduled collector.

Uses a fresh, logged-out Chromium context. No saved session, stealth modifications,
proxies, login-wall workarounds, or private endpoints are used.
"""
import json
import re
import time
from urllib.parse import urlparse

import instagram_reels as ig


def fetch_accounts(accounts):
    from playwright.sync_api import sync_playwright, Error

    output = []
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        context = browser.new_context(locale="en-US")
        page = context.new_page()
        documents = []

        def capture(response):
            host = urlparse(response.url).hostname or ""
            if not host.endswith(".instagram.com") or response.status != 200:
                return
            if "application/json" not in response.headers.get("content-type", ""):
                return
            try:
                data = response.json()
                documents.append('<script type="application/json">' + json.dumps(data) + '</script>')
            except (Error, ValueError):
                pass

        page.on("response", capture)
        for account in accounts:
            try:
                documents.clear()
                page.goto("https://www.instagram.com/%s/reels/" % account, wait_until="domcontentloaded", timeout=25000)
                links = page.locator('a[href*="/reel/"]')
                links.first.wait_for(state="visible", timeout=12000)
                # Only reel links actually displayed on this account's public grid.
                candidates, seen = [], set()
                for link in links.all():
                    match = re.search(r"/reel/([A-Za-z0-9_-]+)/", link.get_attribute("href") or "")
                    if not match or match[1] in seen:
                        continue
                    seen.add(match[1])
                    image = link.locator("img")
                    candidates.append((match[1], image.first.get_attribute("src") if image.count() else ""))
                    if len(candidates) == ig.REELS_PER_ACCOUNT:
                        break
                found = []
                for code, thumbnail in candidates:
                    documents.clear()
                    url = "https://www.instagram.com/reel/%s/" % code
                    page.goto(url, wait_until="domcontentloaded", timeout=25000)
                    try:
                        page.locator("video").first.wait_for(state="attached", timeout=10000)
                    except Error:
                        pass
                    documents.append(page.content())
                    media = None
                    for document in documents:
                        media = ig.parse_media(document, code)
                        if media:
                            break
                        media = next((r for r in ig.page_records(document)
                                      if r.get("code") == code and r.get("product_type") == "clips"
                                      and r.get("video_versions")), None)
                        if media:
                            break
                    if not media:
                        continue
                    versions = [v for v in media.get("video_versions", []) if ig.is_instagram_media(v.get("url", ""))]
                    if not versions:
                        continue
                    thumbnail = media.get("display_uri") or thumbnail or ""
                    if not ig.is_instagram_media(thumbnail):
                        continue
                    caption = (media.get("caption") or {}).get("text", "")
                    found.append({"id": code, "account": account, "url": url,
                                  "caption": caption, "title": caption.split("\n")[0][:120] or "@%s 릴스" % account,
                                  "thumbnail": thumbnail, "videoUrl": versions[0]["url"],
                                  "views": media.get("play_count"), "likes": media.get("like_count"),
                                  "comments": media.get("comment_count"), "takenAt": media.get("taken_at", 0),
                                  "collectedAt": time.time()})
                output.extend(found)
                print("릴스 브라우저 수집: %s %d개" % (account, len(found)), flush=True)
            except Error as error:
                print("릴스 공개 브라우저 조회 실패: %s (%s)" % (account, type(error).__name__), flush=True)
        context.close()
        browser.close()
    return output
