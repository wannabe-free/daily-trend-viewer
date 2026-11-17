import json
import unittest
import io
import os
import tempfile
from unittest.mock import patch

import instagram_reels as ig
import collect


def page(*records):
    return '<script type="application/json">' + json.dumps({"require": list(records)}) + '</script>'


class InstagramReelsTests(unittest.TestCase):
    def test_profile_rejects_other_accounts_photos_and_duplicates(self):
        node = {"code": "Real_123", "product_type": "clips", "play_count": 42,
                "like_count": 7, "comment_count": 2, "display_uri": "https://s.cdninstagram.com/thumb.jpg"}
        records = [{"pk": "1", "username": "wanted", "is_private": False},
                   {"pk": "1", "polaris_clips_connection": {"edges": [
                       {"node": node}, {"node": node}, {"node": {**node, "code": "photo", "product_type": "feed"}}]}},
                   {"pk": "2", "polaris_clips_connection": {"edges": [{"node": {**node, "code": "Wrong"}}]}}]
        result = ig.parse_profile(page(*records), "wanted")
        self.assertEqual([r["id"] for r in result], ["Real_123"])
        self.assertEqual((result[0]["views"], result[0]["likes"], result[0]["comments"]), (42, 7, 2))
        self.assertEqual(ig.parse_profile(page(*records), "different"), [])
        records[0]["is_private"] = True
        self.assertEqual(ig.parse_profile(page(*records), "wanted"), [])

    def test_media_requires_exact_requested_public_reel(self):
        reel = {"code": "Real", "product_type": "clips", "video_versions": []}
        self.assertIsNone(ig.parse_media(page({"xig_polaris_media": {
            "code": "Other", "if_not_gated_logged_out": reel}}), "Real"))
        self.assertIsNone(ig.parse_media(page({"xig_polaris_media": {
            "code": "Real", "if_not_gated_logged_out": None, "suggested": reel}}), "Real"))
        self.assertEqual(ig.parse_media(page({"xig_polaris_media": {
            "code": "Real", "if_not_gated_logged_out": reel}}), "Real"), reel)

    def test_partial_failure_keeps_only_requested_accounts(self):
        current = {"accounts": ["one", "two"], "reels": [{"account": "one", "likes": 10}], "fetchedAt": 30}
        old = {"fetchedAt": 20, "reels": [{"account": "two", "likes": 3}, {"account": "removed", "likes": 100}]}
        result = ig.merge_snapshots(current, [old])
        self.assertEqual([r["account"] for r in result["reels"]], ["one", "two"])
        self.assertEqual(result["cachedAccounts"], ["two"])
        self.assertEqual(result["source"], "mixed")
        self.assertEqual(result["missingAccounts"], [])
        self.assertEqual(result["reels"][1]["collectedAt"], 20)

    def test_only_instagram_https_media_is_accepted(self):
        self.assertTrue(ig.is_instagram_media("https://s.cdninstagram.com/a.mp4"))
        self.assertFalse(ig.is_instagram_media("https://cdninstagram.com.attacker.example/a.mp4"))
        self.assertFalse(ig.is_instagram_media("http://s.cdninstagram.com/a.mp4"))

    def test_static_video_is_saved_and_failed_cached_video_is_removed(self):
        class Response(io.BytesIO):
            def __init__(self, body):
                super().__init__(body)
                self.headers = {"Content-Length": str(len(body))}
        original_out = collect.OUT
        try:
            with tempfile.TemporaryDirectory(prefix=".reel-test-", dir=collect.server.BASE_DIR) as folder:
                self.assertEqual(os.path.commonpath([os.path.realpath(folder), os.path.realpath(collect.server.BASE_DIR)]),
                                 os.path.realpath(collect.server.BASE_DIR))
                collect.OUT = folder
                reel = {"id": "Real", "account": "one", "videoUrl": "https://s.cdninstagram.com/real.mp4"}
                body = b"\x00\x00\x00\x18ftypisom" + b"real media fixture"
                with patch("collect.urllib.request.urlopen", return_value=Response(body)):
                    self.assertEqual(collect.download_reel_media([reel]), 1)
                self.assertTrue(reel["videoUrl"].startswith("media/"))
                with open(os.path.join(folder, reel["videoUrl"]), "rb") as saved:
                    self.assertEqual(saved.read(), body)
                cached = {**reel}
                with patch("collect.urllib.request.urlopen", return_value=Response(b"<html>blocked</html>")):
                    self.assertEqual(collect.download_reel_media([cached]), 0)
                self.assertEqual(cached["videoUrl"], "")
        finally:
            collect.OUT = original_out


if __name__ == "__main__":
    unittest.main()
