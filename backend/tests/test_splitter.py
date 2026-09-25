import unittest

from backend.app.loader import make_document
from backend.app.splitter import (
    text_windows, split_document, fit_token_budget, embedding_text,
)


class SplitterTests(unittest.TestCase):
    def test_no_loss_without_overlap(self):
        text = '甲乙丙丁。' * 50
        pieces = text_windows(text, 31, 0)
        self.assertEqual(''.join(pieces), text)
        self.assertTrue(all(0 < len(p) <= 31 for p in pieces))

    def test_overlap_and_progress_without_punctuation(self):
        text = ''.join(chr(0x4e00 + i) for i in range(120))
        pieces = text_windows(text, 30, 7)
        merged = pieces[0] + ''.join(p[7:] for p in pieces[1:])
        self.assertEqual(merged, text)
        with self.assertRaises(ValueError):
            text_windows(text, 30, 30)

    def test_stable_ids_and_business_isolation(self):
        a = make_document('x.json', '甲', '', 'a', '甲资料。' * 20)
        b = make_document('x.json', '乙', '', 'b', '乙资料。' * 20)
        left = split_document(a, 20, 3)
        right = split_document(b, 20, 3)
        self.assertEqual(left, split_document(a, 20, 3))
        self.assertTrue(set(c.chunk_id for c in left).isdisjoint(c.chunk_id for c in right))
        self.assertTrue(all('乙' not in c.text for c in left))

    def test_token_refinement_preserves_order_and_text(self):
        doc = make_document('x.txt', '短标题', '', 'x', '甲乙丙丁' * 50)
        original = split_document(doc, 500, 0)
        refined = fit_token_budget(original, len, 40)
        self.assertEqual(''.join(c.text for c in refined), doc.text)
        self.assertTrue(all(len(embedding_text(c)) <= 40 for c in refined))
        self.assertEqual(len(set(c.chunk_id for c in refined)), len(refined))


if __name__ == '__main__':
    unittest.main()