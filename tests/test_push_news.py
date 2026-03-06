import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import push_news


class PushNewsTests(unittest.TestCase):
    def test_build_list_page_url_with_default_query_style(self) -> None:
        self.assertEqual(
            push_news.build_list_page_url("https://news.aibase.com/zh/news", 1),
            "https://news.aibase.com/zh/news",
        )
        self.assertEqual(
            push_news.build_list_page_url("https://news.aibase.com/zh/news", 2),
            "https://news.aibase.com/zh/news?page=2",
        )
        self.assertEqual(
            push_news.build_list_page_url("https://example.com/news?lang=zh", 3),
            "https://example.com/news?lang=zh&page=3",
        )

    def test_build_list_page_url_with_template(self) -> None:
        self.assertEqual(
            push_news.build_list_page_url(
                "https://ignored.example.com",
                2,
                template="https://news.aibase.com/zh/news/{page}",
            ),
            "https://news.aibase.com/zh/news/2",
        )
        with self.assertRaises(ValueError):
            push_news.build_list_page_url(
                "https://ignored.example.com",
                2,
                template="https://news.aibase.com/zh/news/page",
            )

    @unittest.skipIf(
        push_news.BeautifulSoup is None,
        "beautifulsoup4 is not installed in local environment",
    )
    def test_extract_news_items_from_html_filters_and_dedupes(self) -> None:
        html = """
        <html><body>
          <a href="/zh/news/101" title="第一条新闻">第一条新闻</a>
          <a href="/zh/news/100">这是一条比较长的标题用于测试去重选择规则</a>
          <a href="/zh/news/100" title="短标题">短标题</a>
          <a href="/zh/news/99" title="AI资讯">AI资讯</a>
          <a href="/zh/news/98" title="最新资讯">最新资讯</a>
          <a href="/zh/news/not-a-number" title="无效">无效</a>
          <a href="/other/path/1" title="其他链接">其他链接</a>
        </body></html>
        """
        items = push_news.extract_news_items_from_html(html)
        self.assertEqual([item[0] for item in items], [101, 100])
        self.assertEqual(items[1][1], "短标题")
        self.assertEqual(items[0][2], "https://news.aibase.com/zh/news/101")

    def test_extract_news_items_from_api_payload(self) -> None:
        payload = {
            "code": 200,
            "msg": "成功",
            "data": {
                "list": [
                    {"oid": 201, "title": "第一条接口新闻"},
                    {"oid": "200", "title": "接口第二条新闻"},
                    {"oid": 200, "title": "接口第二条新闻（更长标题）"},
                    {"oid": "bad", "title": "无效 oid"},
                    {"oid": 199, "title": ""},
                ]
            },
        }
        items = push_news.extract_news_items_from_api_payload(payload)
        self.assertEqual([item[0] for item in items], [201, 200])
        self.assertEqual(items[1][1], "接口第二条新闻")
        self.assertEqual(items[0][2], "https://news.aibase.com/zh/news/201")

    def test_load_state_raises_on_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "state.json"
            path.write_text("{bad json", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                push_news.load_state(path)

    def test_load_state_filters_non_numeric_seen_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "state.json"
            path.write_text(
                json.dumps({"seen_ids": ["101", "00100", "bad", "", 99, None]}),
                encoding="utf-8",
            )
            state = push_news.load_state(path)
            self.assertEqual(state["seen_ids"], ["101", "100", "99"])

    def test_split_batches(self) -> None:
        items = [
            (105, "a", "u1"),
            (104, "b", "u2"),
            (103, "c", "u3"),
            (102, "d", "u4"),
            (101, "e", "u5"),
        ]
        self.assertEqual(len(push_news.split_batches(items, 0)), 1)
        self.assertEqual(len(push_news.split_batches(items, -1)), 1)
        batches = push_news.split_batches(items, 2)
        self.assertEqual([len(batch) for batch in batches], [2, 2, 1])
        self.assertEqual(push_news.split_batches([], 2), [])

    def test_fetch_news_keeps_previous_pages_when_later_page_fails(self) -> None:
        original_fetch_page_api = push_news.fetch_news_page_api
        try:
            def fake_fetch_page_api(page_no: int):
                if page_no == 1:
                    return [(101, "新闻 101", "u101"), (100, "新闻 100", "u100")]
                raise RuntimeError("page fetch failed")

            push_news.fetch_news_page_api = fake_fetch_page_api
            with contextlib.redirect_stderr(io.StringIO()):
                items = push_news.fetch_news(max_pages=3, source="api")
            self.assertEqual([item[0] for item in items], [101, 100])
        finally:
            push_news.fetch_news_page_api = original_fetch_page_api

    def test_deliver_batches_persists_successful_batches_before_failure(self) -> None:
        original_post = push_news.post_to_feishu
        try:
            calls = []

            def fake_post(_webhook: str, items):
                calls.append([item[0] for item in items])
                if len(calls) == 2:
                    raise RuntimeError("second batch failed")

            push_news.post_to_feishu = fake_post
            with tempfile.TemporaryDirectory() as tmp_dir:
                state_path = Path(tmp_dir) / "state.json"
                state = {"seen_ids": []}
                items = [
                    (105, "a", "u1"),
                    (104, "b", "u2"),
                    (103, "c", "u3"),
                ]
                with self.assertRaises(RuntimeError):
                    with contextlib.redirect_stdout(io.StringIO()):
                        push_news.deliver_batches(
                            "webhook",
                            items,
                            state,
                            state_path,
                            batch_size=2,
                        )

                persisted = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(persisted["seen_ids"], ["105", "104"])
                self.assertEqual(calls, [[105, 104], [103]])
        finally:
            push_news.post_to_feishu = original_post


if __name__ == "__main__":
    unittest.main()
