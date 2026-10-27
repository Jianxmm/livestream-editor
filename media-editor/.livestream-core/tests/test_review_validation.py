"""Reject invalid review dates and non-finite durations before storing records."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from review_validation import valid_review_date
from track_human_review import freeze_baseline,summarize_human_review
from audit_draft import audit_human_review_metrics


class ReviewValidationTests(unittest.TestCase):
    def test_calendar_dates(self):
        for value in ['2026-99-99','2026-02-29','2026-04-31','20261023','待填',None]:
            self.assertFalse(valid_review_date(value),value)
        self.assertTrue(valid_review_date('2024-02-29'))
        self.assertTrue(valid_review_date('2026-10-23'))

    def test_bad_values_cannot_write_human_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            case=Path(directory);(case/'inputs').mkdir();(case/'outputs').mkdir()
            (case/'inputs/02_带时间码原稿.md').write_text('发言人1 00:00\n合成原话',encoding='utf-8')
            (case/'outputs/01_公众号可发布稿.md').write_text('合成正文',encoding='utf-8')
            freeze_baseline(case)
            for value,minutes in [('2026-99-99',5),('2026-10-23',float('nan')),('2026-10-23',float('inf'))]:
                with self.assertRaises(ValueError):summarize_human_review(case,'合成测试记录人',value,minutes)
                self.assertFalse((case/'_work/人工终审修改量.json').exists())
            summarize_human_review(case,'合成测试记录人','2026-10-23',5)
            path=case/'_work/人工终审修改量.json';data=json.loads(path.read_text());data['review_date']='2026-99-99';path.write_text(json.dumps(data),encoding='utf-8')
            errors=audit_human_review_metrics(case,'- 终审人：合成测试记录人\n- 终审日期：2026-99-99\n- 人工终审耗时：5分钟\n')
            self.assertTrue(any(f.code=='RELEASE_REVIEW_DATE_INVALID' for f in errors))


if __name__=='__main__':unittest.main()
