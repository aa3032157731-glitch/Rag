import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.loader import load_documents


class LoaderTests(unittest.TestCase):
    def test_sources_bom_and_json_records(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'a').mkdir()
            (root / 'b').mkdir()
            (root / 'a/x.txt').write_text('中文资料', encoding='utf-8-sig')
            (root / 'b/x.txt').write_text('另一份中文资料', encoding='utf-8')
            (root / 'x.json').write_text('[{"id":"a","content":"甲"},{"id":"b","content":"乙"}]',
                                         encoding='utf-8',)
            report = load_documents(root)
            self.assertFalse(report.errors)
            self.assertEqual(len(report.documents), 4)
            self.assertEqual(len({d.document_id for d in report.documents}), 4)
            self.assertNotIn('\ufeff', report.documents[0].text)

    def test_bad_files_do_not_hide_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'bad.json').write_text('{', encoding='utf-8')
            (root / 'empty.txt').write_text('', encoding='utf-8')
            (root / 'skip.pdf').write_bytes(b'fake')
            report = load_documents(root)
            self.assertEqual(len(report.errors), 2)
            self.assertEqual(len(report.skipped), 1)
    def test_markdown_sections_and_stability(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'x.md').write_text(
                '# 总标题\n## 时间\n八点\n## 地点\n一楼',
                encoding='utf-8'
            )
            first = load_documents(root).documents
            self.assertEqual([d.section for d in first], ['时间', '地点'])
            self.assertEqual(first, load_documents(root).documents)

if __name__ == '__main__':
    unittest.main()
















