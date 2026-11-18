"""Read reel metadata exposed in Instagram's public web-page JSON, without login."""
import json
import re
import time
import urllib.error
from urllib.parse import quote, urlparse

PUBLIC_HEADERS = {"User-Agent": "Mozilla/5.0", "X-IG-App-ID": "936619743392459",
                  "Accept": "application/json,text/html"}
REELS_PER_ACCOUNT = 6


def page_records(page):
    def walk(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk(child)
    for script in re.findall(r'''<script\b[^>]*type=["']application/json["'][^>]*>(.*?)</script>''', page, re.S):
        try:
            yield from walk(json.loads(script))
        except (ValueError, RecursionError):
            continue


def parse_profile(page, username):
    records = list(page_records(page))
    profile = next((r for r in records if str(r.get("username", "")).lower() == username.lower()
                    and "is_private" in r), None)
    if not profile or profile.get("is_private"):
        return []
    reels, seen = [], set()
    for record in records:
        if str(record.get("pk")) != str(profile.get("pk")):
            continue
        for edge in (record.get("polaris_clips_connection") or {}).get("edges", []):
            node = edge.get("node") or {}
            code = node.get("code", "")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", code) or code in seen or node.get("product_type") != "clips":
                continue
            seen.add(code)
            hidden = node.get("like_and_view_counts_disabled")
            reels.append({
                "id": code, "account": username, "title": "@%s 릴스" % username,
                "caption": "", "profileDescription": profile.get("biography", ""),
                "views": None if hidden else node.get("play_count"),
                "likes": None if hidden else node.get("like_count"), "comments": node.get("comment_count"),
                "thumbnail": node.get("display_uri", ""), "videoUrl": "",
                "url": "https://www.instagram.com/reel/%s/" % code,
                "takenAt": 0, "collectedAt": time.time(),
            })
    return reels[:REELS_PER_ACCOUNT]


def parse_media(page, code):
    for record in page_records(page):
        media = record.get("xig_polaris_media")
        if not isinstance(media, dict) or media.get("code") != code:
            continue
        public = media.get("if_not_gated_logged_out")
        # Match the requested reel, never a suggested post or gated content.
        if isinstance(public, dict) and public.get("code") == code and public.get("product_type") == "clips":
            return public
    return None


def is_instagram_media(url):
    host = urlparse(url).hostname or ""
    return url.startswith("https://") and host.endswith((".cdninstagram.com", ".fbcdn.net"))


def merge_snapshots(current, snapshots):
    """Keep current data; fill only missing accounts from their newest saved snapshot."""
    accounts = current["accounts"]
    reels = [dict(r) for r in current.get("reels", []) if r.get("account") in accounts]
    cached_accounts = []
    snapshots = sorted((s for s in snapshots if isinstance(s, dict)),
                       key=lambda s: s.get("fetchedAt", 0), reverse=True)
    for account in accounts:
        if any(r["account"] == account for r in reels):
            continue
        for snapshot in snapshots:
            saved = [r for r in snapshot.get("reels", []) if r.get("account") == account]
            if saved:
                reels.extend({**r, "cached": True, "collectedAt": r.get("collectedAt", snapshot.get("fetchedAt"))}
                             for r in saved[:REELS_PER_ACCOUNT])
                cached_accounts.append(account)
                break
    reels.sort(key=lambda r: r.get("likes") or 0, reverse=True)
    if cached_accounts:
        source = "snapshot" if all(r.get("cached") for r in reels) else "mixed"
    else:
        source = current.get("source", "live")
    return {**current, "reels": reels, "source": source, "cachedAccounts": cached_accounts,
            "missingAccounts": [a for a in accounts if not any(r["account"] == a for r in reels)]}


def fetch_reels(username, http_get):
    try:
        _, body = http_get("https://www.instagram.com/%s/reels/" % quote(username),
                           headers=PUBLIC_HEADERS, timeout=20)
        reels = parse_profile(body.decode("utf-8", "ignore"), username)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return []
    for reel in reels:
        try:
            _, body = http_get(reel["url"], headers=PUBLIC_HEADERS, timeout=15)
            media = parse_media(body.decode("utf-8", "ignore"), reel["id"])
            if not media:
                continue
            caption = (media.get("caption") or {}).get("text", "")
            versions = [v for v in (media.get("video_versions") or []) if is_instagram_media(v.get("url", ""))]
            reel.update({"caption": caption, "title": caption.split("\n")[0][:120] or reel["title"],
                         "takenAt": media.get("taken_at", 0),
                         "videoUrl": max(versions, key=lambda v: v.get("width", 0))["url"] if versions else ""})
        except (urllib.error.URLError, TimeoutError, ValueError):
            continue
    return [r for r in reels if r.get("videoUrl") and is_instagram_media(r.get("thumbnail", ""))]
