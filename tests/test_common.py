from __future__ import annotations

import unittest

from plugins.common import extract_openclaw_text, format_qq_chat_text


class CommonFormattingTests(unittest.TestCase):
    def test_format_qq_chat_text_converts_markdown(self) -> None:
        source = (
            "## 标题\n\n"
            "- [ ] 未完成事项\n"
            "- 普通列表\n"
            "1) 第一条\n"
            "```python\nprint('hi')\n```\n"
            "[官网](https://example.com)"
        )
        self.assertEqual(
            format_qq_chat_text(source),
            "标题\n\n[未完成] 未完成事项\n• 普通列表\n1. 第一条\nprint('hi')\n官网：https://example.com",
        )

    def test_extract_openclaw_text_preserves_newlines(self) -> None:
        payload = {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": "标题\n\n1. 第一条\n2. 第二条",
                        }
                    ]
                }
            ]
        }
        self.assertEqual(extract_openclaw_text(payload), "标题\n\n1. 第一条\n2. 第二条")


if __name__ == "__main__":
    unittest.main()
