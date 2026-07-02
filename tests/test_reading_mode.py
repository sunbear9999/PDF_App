import sys
import unittest

try:
    from PySide6.QtWidgets import QApplication
    from core.reading_mode import (
        ReadingLine,
        ReadingModeController,
        ReadingPage,
        ReadingTextCleaner,
        ReadingTextExtractor,
        ReadingToken,
        ReadingTokenStream,
        ReadingWord,
    )
except ModuleNotFoundError:  # pragma: no cover - local CI may omit Qt.
    QApplication = None


class _Rect:
    width = 600
    height = 800


class _Page:
    def __init__(self, words):
        self.rect = _Rect()
        self._words = words

    def get_text(self, mode, sort=False):
        if mode == "words":
            return list(self._words)
        return ""


class _Doc:
    def __init__(self, pages):
        self._pages = pages

    def __len__(self):
        return len(self._pages)

    def load_page(self, page_num):
        return self._pages[page_num]


def _word(text, y=120, x=50, page_num=0, index=0):
    return ReadingWord(
        text=text,
        page_num=page_num,
        word_index=index,
        line_key=(0, 0, page_num),
        bbox=(x, y, x + max(10, len(text) * 5), y + 12),
        page_width=600,
        page_height=800,
    )


def _line(text, y=120, page_num=0):
    words = tuple(_word(part, y=y, x=50 + i * 40, page_num=page_num, index=i) for i, part in enumerate(text.split()))
    return ReadingLine(text=text, page_num=page_num, words=words, bbox=(50, y, 500, y + 12))


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class TestReadingMode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_extractor_orders_words_across_pages(self):
        doc = _Doc([
            _Page([
                (80, 150, 120, 162, "second", 0, 1, 1),
                (50, 120, 75, 132, "first", 0, 0, 0),
            ]),
            _Page([(50, 120, 75, 132, "third", 0, 0, 0)]),
        ])

        pages = ReadingTextExtractor().extract(doc)
        text = " ".join(line.text for page in pages for line in page.lines)

        self.assertEqual(text, "first second third")

    def test_cleaner_removes_margins_page_numbers_and_leaders(self):
        page = ReadingPage(
            page_num=0,
            width=600,
            height=800,
            lines=(
                _line("Running Title", y=20),
                _line("This is real body text.", y=160),
                _line("Chapter One ........ 9", y=220),
                _line("12", y=760),
            ),
        )

        cleaned = ReadingTextCleaner().clean([page])
        lines = [line.text for line in cleaned[0].lines]

        self.assertEqual(lines, ["This is real body text."])

    def test_cleaner_removes_repeated_running_lines_but_keeps_body_chapter_title(self):
        pages = []
        for page_num in range(4):
            pages.append(
                ReadingPage(
                    page_num=page_num,
                    width=600,
                    height=800,
                    lines=(
                        _line("Shared Running Header", y=90, page_num=page_num),
                        _line("Chapter Title", y=180, page_num=page_num),
                        _line("Body prose continues normally.", y=220, page_num=page_num),
                    ),
                )
            )

        cleaned = ReadingTextCleaner().clean(pages)
        all_lines = [line.text for page in cleaned for line in page.lines]

        self.assertNotIn("Shared Running Header", all_lines)
        self.assertIn("Chapter Title", all_lines)

    def test_cleaner_removes_case_title_running_headers(self):
        pages = []
        for page_num in range(3):
            pages.append(
                ReadingPage(
                    page_num=page_num,
                    width=600,
                    height=800,
                    lines=(
                        _line("Smith v. Jones", y=125, page_num=page_num),
                        _line("The court explains the rule in context.", y=210, page_num=page_num),
                    ),
                )
            )

        cleaned = ReadingTextCleaner().clean(pages)
        all_lines = [line.text for page in cleaned for line in page.lines]

        self.assertNotIn("Smith v. Jones", all_lines)
        self.assertIn("The court explains the rule in context.", all_lines)

    def test_cleaner_skips_links_and_reference_back_matter_but_keeps_inline_citations(self):
        pages = [
            ReadingPage(
                page_num=0,
                width=600,
                height=800,
                lines=(
                    _line("This point remains supported (Smith, 2020).", y=180, page_num=0),
                    _line("Available at https://example.com/source", y=240, page_num=0),
                ),
            ),
            ReadingPage(
                page_num=1,
                width=600,
                height=800,
                lines=(
                    _line("More body text continues here.", y=180, page_num=1),
                ),
            ),
            ReadingPage(
                page_num=2,
                width=600,
                height=800,
                lines=(
                    _line("References", y=120, page_num=2),
                    _line("Smith, A. 2020. Full bibliographic entry.", y=180, page_num=2),
                ),
            ),
        ]

        cleaned = ReadingTextCleaner().clean(pages)
        all_lines = [line.text for page in cleaned for line in page.lines]

        self.assertIn("This point remains supported (Smith, 2020).", all_lines)
        self.assertNotIn("Available at https://example.com/source", all_lines)
        self.assertNotIn("References", all_lines)
        self.assertNotIn("Smith, A. 2020. Full bibliographic entry.", all_lines)

    def test_plugin_filter_and_transform_order_and_failure_isolation(self):
        page = ReadingPage(0, 600, 800, (_line("Keep me."),))
        calls = []

        def first_filter(pages):
            calls.append("first")
            return pages

        def bad_filter(pages):
            calls.append("bad")
            raise RuntimeError("boom")

        cleaned = ReadingTextCleaner().clean([page], text_filters=[first_filter, bad_filter])
        tokens = ReadingTokenStream().build(
            cleaned,
            token_transforms=[
                lambda toks: toks + [ReadingToken("extra", 0, len(toks), 0, 99, (0, 0, 1, 1))]
            ],
        )

        self.assertEqual(calls, ["first", "bad"])
        self.assertEqual([token.text for token in tokens], ["Keep", "me.", "extra"])

    def test_controller_speed_delay_and_bounds(self):
        controller = ReadingModeController()
        controller.tokens = [
            ReadingToken("short", 0, 0, 0, 0, (0, 0, 1, 1)),
            ReadingToken("sentence.", 0, 1, 0, 1, (0, 0, 1, 1)),
        ]
        controller.set_wpm(300)

        self.assertEqual(controller.token_delay_ms(controller.tokens[0]), 200)
        self.assertGreater(controller.token_delay_ms(controller.tokens[1]), 200)

        controller.seek_token(99)
        self.assertEqual(controller.current_index, 1)
        controller.step_words(-99)
        self.assertEqual(controller.current_index, 0)

    def test_controller_restores_position_by_pdf_path(self):
        doc = _Doc([_Page([
            (50, 120, 75, 132, "One", 0, 0, 0),
            (80, 120, 105, 132, "two.", 0, 0, 1),
        ])])
        controller = ReadingModeController()
        self.assertTrue(controller.load_pdf("a.pdf", doc))
        controller.seek_token(1)
        self.assertTrue(controller.load_pdf("a.pdf", doc))

        self.assertEqual(controller.current_index, 1)


if __name__ == "__main__":
    unittest.main()
