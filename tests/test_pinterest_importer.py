import os
import tempfile
import unittest
from http.cookiejar import MozillaCookieJar

from core.pinterest_importer import (
    DEFAULT_MIN_ASPECT,
    extract_from_json_payload,
    extract_hydration_images,
    extract_pin_links,
    extract_pinimg_candidates,
    extract_hydration_script,
    is_pinterest_page_url,
    parse_search_query,
    _normalize_pinimg_url,
    _safe_parse_json,
)


class _CookieFileMaker:
    """临时生成 Netscape 格式 cookie 文件。"""

    @staticmethod
    def write(content: str) -> str:
        fd, path = tempfile.mkstemp(suffix="_cookies.txt")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path


class PinterestImporterTests(unittest.TestCase):
    def test_extracts_and_deduplicates_image_sizes(self):
        page = r'''
            <img src="https://i.pinimg.com/236x/aa/bb/test.jpg">
            <script>{
              "url":"https:\/\/i.pinimg.com\/736x\/aa\/bb\/test.jpg",
              "other":"https:\/\/i.pinimg.com\/originals\/cc\/dd\/other.png",
              "avatar":"https:\/\/i.pinimg.com\/75x75_RS\/ee\/ff\/avatar.jpg"
            }</script>
        '''
        candidates = extract_pinimg_candidates(page)
        self.assertEqual(2, len(candidates))
        self.assertEqual(
            "https://i.pinimg.com/originals/aa/bb/test.jpg",
            candidates[0]["preferred_url"],
        )
        self.assertEqual(
            "https://i.pinimg.com/736x/aa/bb/test.jpg",
            candidates[0]["fallback_url"],
        )

    def test_extracts_pin_detail_links(self):
        page = (
            '<a href="/pin/123456789/">Pin</a>'
            '<a href="/pin/a-nice-room--987654321/">Slug Pin</a>'
        )
        self.assertEqual(
            [
                "https://www.pinterest.com/pin/123456789/",
                "https://www.pinterest.com/pin/a-nice-room--987654321/",
            ],
            extract_pin_links("https://www.pinterest.com/board/demo/", page),
        )

    def test_restricts_page_hosts(self):
        self.assertTrue(is_pinterest_page_url("https://pin.it/abc"))
        self.assertTrue(is_pinterest_page_url("https://www.pinterest.com/pin/1/"))
        self.assertTrue(is_pinterest_page_url("https://www.pinterest.co.uk/demo/"))
        self.assertFalse(is_pinterest_page_url("https://example.com/pinterest"))
        self.assertFalse(is_pinterest_page_url("file:///tmp/pinterest.html"))

    def test_search_page_url_passes(self):
        """搜索页 URL 应通过验证。"""
        self.assertTrue(is_pinterest_page_url(
            "https://www.pinterest.com/search/pins/?q=spider%20man&rs=ac"))

    def test_normalize_protocol_relative(self):
        """协议相对 URL 应被标准化。"""
        self.assertEqual(
            "https://i.pinimg.com/originals/ab/cd/test.jpg",
            _normalize_pinimg_url("//i.pinimg.com/originals/ab/cd/test.jpg"),
        )

    def test_normalize_http(self):
        self.assertEqual(
            "https://i.pinimg.com/originals/ab/cd/test.jpg",
            _normalize_pinimg_url("http://i.pinimg.com/originals/ab/cd/test.jpg"),
        )

    def test_extract_hydration_from_search_page(self):
        """搜索页 hydration JSON 中的图片应被提取。"""
        page = r'''
        <html>
        <script id="__PWS_DATA__" type="application/json">
        {
          "resourceResponses": [{
            "response": {
              "data": {
                "results": [{
                  "images": {
                    "orig": {"url": "https://i.pinimg.com/originals/ab/cd/test1.jpg"},
                    "736x": {"url": "https://i.pinimg.com/736x/ab/cd/test1.jpg"}
                  }
                }, {
                  "images": {
                    "orig": {"url": "https://i.pinimg.com/originals/ef/gh/test2.png"}
                  }
                }]
              }
            }
          }]
        }
        </script>
        </html>
        '''
        candidates = extract_hydration_images(page)
        self.assertEqual(2, len(candidates),
                         f"Expected 2 unique images, got {candidates}")

    def test_aspect_ratio_default(self):
        """默认竖屏比例 ≈ 1.778。"""
        self.assertAlmostEqual(DEFAULT_MIN_ASPECT, 16.0 / 9.0, places=3)

    def test_safe_parse_json_strips_xssi_prefix(self):
        """Pinterest 经常用 )]}' 防 XSSI 注入。"""
        text = ")]}'\n{\"foo\":\"bar\",\"images\":{\"orig\":{\"url\":\"https://i.pinimg.com/originals/ab/cd/x.jpg\"}}}"
        parsed = _safe_parse_json(text)
        self.assertIsNotNone(parsed)
        self.assertEqual("bar", parsed["foo"])

    def test_extract_from_json_payload(self):
        """深度嵌套的 JSON 树里的图片 URL 应被提取。"""
        payload = '''
        {
          "resourceResponses": [
            {
              "name": "BaseSearchResource",
              "response": {
                "data": {
                  "results": [
                    {
                      "id": "1",
                      "images": {
                        "orig": {"url": "https://i.pinimg.com/originals/aa/bb/img1.jpg"},
                        "236x": {"url": "https://i.pinimg.com/236x/aa/bb/img1.jpg"}
                      }
                    },
                    {
                      "id": "2",
                      "images": {
                        "orig": {"url": "https://i.pinimg.com/originals/cc/dd/img2.png"}
                      }
                    }
                  ]
                }
              }
            }
          ]
        }
        '''
        candidates = extract_from_json_payload(payload)
        self.assertEqual(2, len(candidates),
                         f"Expected 2 unique images, got {candidates}")

    def test_extract_hydration_script_finds_pws_data(self):
        """__PWS_DATA__ script 块应被定位。"""
        page = '''
        <html><head>
        <script id="__PWS_DATA__" type="application/json">
        {"foo":"bar","images":{"orig":{"url":"https://i.pinimg.com/originals/x/y/z.jpg"}}}
        </script>
        </head><body></body></html>
        '''
        script = extract_hydration_script(page)
        self.assertIsNotNone(script)
        self.assertIn('"foo":"bar"', script)
        self.assertIn("pinimg.com", script)

    def test_parse_search_query(self):
        """从搜索 URL 提取查询关键词。"""
        self.assertEqual(
            "spider man wallpaper",
            parse_search_query(
                "https://www.pinterest.com/search/pins/?q=spider+man+wallpaper&rs=ac"
            ),
        )
        self.assertEqual(
            "spider man",
            parse_search_query(
                "https://www.pinterest.com/search/pins/?q=spider%20man"
            ),
        )
        self.assertIsNone(parse_search_query(
            "https://www.pinterest.com/board/demo/"))

    def test_load_cookies_loads_all_domains(self):
        """所有站点 cookie 都加载，但 _get_csrf_token 只返回 Pinterest 的。"""
        from core.pinterest_importer import PinterestImporter
        content = "\n".join([
            "# Netscape HTTP Cookie File",
            ".pinterest.com\tTRUE\t/\tTRUE\t1813473832\t_pinterest_sess\tPIN_SESS",
            "www.pinterest.com\tFALSE\t/\tTRUE\t1797331222\tcsrftoken\tPIN_CSRF",
            ".threads.com\tTRUE\t/\tTRUE\t1802509916\tcsrftoken\tTHREADS_CSRF",
            "ads.tiktok.com\tFALSE\t/\tTRUE\t1815622188\tcsrftoken\tTIKTOK_CSRF",
            ".youtube.com\tTRUE\t/\tTRUE\t1784088845\tsession\tYT_SESS",
        ])

        path = _CookieFileMaker.write(content)
        try:
            importer = PinterestImporter(cookie_file=path)
            session_names = sorted(c.name for c in importer.session.cookies)
            # 所有域名的 cookie 都应加载（YT/TT 也需要用）
            self.assertIn("csrftoken", session_names)
            self.assertIn("_pinterest_sess", session_names)
            self.assertIn("session", session_names)
            # _get_csrf_token 必须返回 Pinterest 的而不是其他站点的
            self.assertEqual("PIN_CSRF", importer._get_csrf_token())
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
