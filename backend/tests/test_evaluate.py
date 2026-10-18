import unittest

from backend.app.models import Hit
from backend.scripts.evaluate import judge, summarize


def hit(text, score):
    return Hit('id', 'x.txt', '标题', '', 'k', text, score)


class EvaluateTests(unittest.TestCase):
    def test_rank_is_first_hit_containing_answer(self):
        hits = [hit('食堂七点开门', 0.7), hit('图书馆 22:00 关门', 0.6)]
        row = judge({'question': '图书馆几点关门', 'answer': '22:00'}, hits, 0.5)
        self.assertEqual(row['rank'], 2)
        self.assertTrue(row['ok'])

    def test_answer_below_threshold_is_not_ok(self):
        row = judge({'question': 'q', 'answer': '22:00'}, [hit('22:00 关门', 0.4)], 0.5)
        self.assertEqual(row['rank'], 1)
        self.assertFalse(row['ok'])

    def test_unanswerable_ok_only_when_rejected(self):
        case = {'question': '天气', 'answer': None}
        self.assertTrue(judge(case, [hit('无关', 0.3)], 0.5)['ok'])
        self.assertFalse(judge(case, [hit('无关', 0.6)], 0.5)['ok'])
        self.assertTrue(judge(case, [], 0.5)['ok'])

    def test_summary_uses_separate_denominators(self):
        rows = [
            {'answerable': True, 'rank': 1, 'ok': True},
            {'answerable': True, 'rank': 3, 'ok': True},
            {'answerable': True, 'rank': None, 'ok': False},
            {'answerable': False, 'rank': None, 'ok': True},
        ]
        summary = summarize(rows)
        self.assertEqual(summary['hit@1'], 0.333)
        self.assertEqual(summary['hit@3'], 0.667)
        self.assertEqual(summary['rejected_unanswerable'], 1)


if __name__ == '__main__':
    unittest.main()
