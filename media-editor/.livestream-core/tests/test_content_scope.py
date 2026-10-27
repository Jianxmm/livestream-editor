"""Effective-content ratios retain evidence and reject untraceable deductions."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from content_scope import read_content_scope
from route_livestream import file_sha256
from audit_draft import audit_full_draft
from audit_solo_share import audit_case as audit_solo


class ContentScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.case=Path(self.temp.name)
        for name in ['inputs','outputs','_work']:(self.case/name).mkdir()
        self.a='有效观点：解释具体方法和适用条件。'*30
        self.b='有效例子：保留发生经过、反例和限制。'*30
        self.noise='合成关播段：请看镜头，检查摆位，然后截图。'*55
        self.source=f'发言人1   00:00\n{self.a}\n\n发言人1   01:00\n{self.b}\n\n发言人1   02:00\n{self.noise}\n'
        self.full=f'<!-- SOLO_BODY_START -->\n{self.a}\n\n{self.b}\n\n{self.noise}\n<!-- SOLO_BODY_END -->'
        (self.case/'inputs/02_带时间码原稿.md').write_text(self.source,encoding='utf-8')
        (self.case/'_work/完整编辑底稿.md').write_text(self.full,encoding='utf-8')
        (self.case/'_work/内容覆盖表.md').write_text('| 00:00–00:59 | 方法 | A |\n| 01:00–01:59 | 例子 | B |\n| 02:00–02:59 | 截图关播 | C |',encoding='utf-8')
        self.entry={'id':'C001','start':'02:00','end':'02:00','category':'关播尾声','source_excerpt':self.noise,'full_draft_excerpt':self.noise,'basis':'合成夹具：只有截图摆位，无主题内容','status':'待确认'}
        self.ledger={'version':1,'content_scope':{'version':1,'source_sha256':file_sha256(self.case/'inputs/02_带时间码原稿.md'),'full_draft_sha256':file_sha256(self.case/'_work/完整编辑底稿.md'),'exclusions':[self.entry]}}
        self.save()

    def save(self):
        (self.case/'_work/编辑决策台账.json').write_text(json.dumps(self.ledger,ensure_ascii=False),encoding='utf-8')

    def tearDown(self):self.temp.cleanup()

    def test_valid_exclusion_changes_only_transient_text_and_keeps_pending_warning(self):
        before=(self.case/'inputs/02_带时间码原稿.md').read_bytes()
        scope=read_content_scope(self.case,self.source,self.full)
        self.assertEqual(scope.errors,[])
        self.assertEqual(len(scope.warnings),1)
        self.assertNotIn(self.noise,scope.source)
        self.assertIn(self.a,scope.source)
        self.assertNotIn(self.noise,scope.full_draft)
        self.assertEqual((self.case/'inputs/02_带时间码原稿.md').read_bytes(),before)

    def test_unconfirmed_exclusion_cannot_complete_handoff(self):
        self.assertTrue(read_content_scope(self.case,self.source,self.full,handoff=True).errors)
        self.entry.update(status='已回听',reviewer='合成测试记录人',review_date='2026-10-23');self.save()
        self.assertEqual(read_content_scope(self.case,self.source,self.full,handoff=True).errors,[])
        self.entry['review_date']='2026-99-99';self.save()
        self.assertTrue(read_content_scope(self.case,self.source,self.full,handoff=True).errors)

    def test_ab_content_cannot_be_removed_through_c_annotation(self):
        self.entry.update(start='00:00',end='00:00',source_excerpt=self.a,full_draft_excerpt=self.a);self.save()
        scope=read_content_scope(self.case,self.source,self.full)
        self.assertTrue(scope.errors)
        self.assertEqual(scope.source,self.source)

    def test_out_of_range_or_unrelated_source_excerpt_rejected(self):
        for changes in [{'start':'09:00'},{'source_excerpt':self.a},{'full_draft_excerpt':'底稿中没有的句子'},{'category':'不够精彩'}]:
            old=self.entry.copy();self.entry.update(changes);self.save()
            self.assertTrue(read_content_scope(self.case,self.source,self.full).errors,changes)
            self.entry.clear();self.entry.update(old)

    def test_changed_input_or_full_draft_invalidates_scope(self):
        for name in ['inputs/02_带时间码原稿.md','_work/完整编辑底稿.md']:
            path=self.case/name;old=path.read_bytes();path.write_bytes(old+b'changed')
            self.assertTrue(read_content_scope(self.case,self.source,self.full).errors)
            path.write_bytes(old)

    def test_full_exclusion_cannot_silently_include_effective_content(self):
        self.entry.update(full_draft_excerpt=self.b+'\n\n'+self.noise,
                          status='已回听', reviewer='合成测试记录人', review_date='2026-10-23')
        self.save()
        scope=read_content_scope(self.case,self.source,self.full,handoff=True)
        self.assertTrue(scope.errors)
        self.assertEqual(scope.full_draft,self.full)
        self.assertEqual(scope.source,self.source)

    def test_punctuation_and_spacing_cleanup_keeps_same_c_provenance(self):
        cleaned=self.noise.replace('，','').replace('。','！ ')
        full=self.full.replace(self.noise,cleaned)
        (self.case/'_work/完整编辑底稿.md').write_text(full,encoding='utf-8')
        self.entry['full_draft_excerpt']=cleaned
        self.ledger['content_scope']['full_draft_sha256']=file_sha256(self.case/'_work/完整编辑底稿.md')
        self.save()
        scope=read_content_scope(self.case,self.source,full)
        self.assertEqual(scope.errors,[])
        self.assertIn(self.b,scope.full_draft)
        self.assertNotIn(cleaned,scope.full_draft)

    def test_duplicate_exclusion_cannot_double_discount_content(self):
        other=dict(self.entry,id='C002')
        self.ledger['content_scope']['exclusions'].append(other);self.save()
        self.assertTrue(read_content_scope(self.case,self.source,self.full).errors)

    def test_original_ratios_stay_in_force_without_scope(self):
        self.ledger.pop('content_scope');self.save()
        scope=read_content_scope(self.case,self.source,self.full)
        self.assertEqual(scope.source,self.source)
        self.assertEqual(scope.full_draft,self.full)
        published='**甲：** '+self.a+'\n\n**乙：** '+self.b
        full=published+'\n\n**甲：** '+self.noise
        self.assertTrue(any(f.code=='PUBLISHED_DROPS_FULL_DRAFT' for f in audit_full_draft(full,published,self.case/'full.md')))

    def test_dual_ratio_uses_validated_effective_full_text(self):
        published='**甲：** '+self.a+'\n\n**乙：** '+self.b
        full=published+'\n\n**甲：** '+self.noise
        (self.case/'_work/完整编辑底稿.md').write_text(full,encoding='utf-8')
        self.ledger['content_scope']['full_draft_sha256']=file_sha256(self.case/'_work/完整编辑底稿.md');self.save()
        scope=read_content_scope(self.case,self.source,full)
        self.assertEqual(scope.errors,[])
        self.assertEqual(audit_full_draft(scope.full_draft,published,self.case/'full.md'),[])
        thin='**甲：** '+self.a[:50]+'\n\n**乙：** '+self.b[:50]
        self.assertTrue(audit_full_draft(scope.full_draft,thin,self.case/'full.md'))

    def test_solo_ratio_excludes_validated_noise_but_still_rejects_lost_effective_content(self):
        body=f'<!-- SOLO_BODY_START -->\n**示例讲者：** {self.a}\n\n{self.b}\n<!-- SOLO_BODY_END -->'
        (self.case/'outputs/01_公众号可发布稿.md').write_text('# 合成分享\n'+body,encoding='utf-8')
        for name,text in [('inputs/03_人物与专名表.md','# 合成'),('outputs/02_待核问题清单.md','# 合成'),('outputs/03_编辑质检报告.md','- 内容类型：单人开营分享\n'),('_work/证据映射.json','{"sections":[]}')]:
            (self.case/name).write_text(text,encoding='utf-8')
        result=audit_solo(self.case)
        self.assertFalse(any('SOURCE_RETENTION_LOW' in e or 'FULL_DRAFT_RETENTION_LOW' in e for e in result.errors),result.errors)
        (self.case/'outputs/01_公众号可发布稿.md').write_text('# 合成分享\n<!-- SOLO_BODY_START -->\n**示例讲者：** 极短摘要\n<!-- SOLO_BODY_END -->',encoding='utf-8')
        result=audit_solo(self.case)
        self.assertTrue(any('SOURCE_RETENTION_LOW' in e for e in result.errors))


if __name__=='__main__':unittest.main()
