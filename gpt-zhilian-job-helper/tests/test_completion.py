"""Conclusive exclusions close work, without certifying unexamined evidence."""
import copy
import unittest

from test_public import (TAGS, configured, card, batch, acquire, stamp,
                         company_payload)
from core import EXPERIENCE, now
from workflow import begin, observe, results, pending, checked_before
from zhilian import sync_applied


def reject_design(state, action='exclude'):
    state['config']['rules'] = [
        {'id': 'duties', 'field': 'description', 'op': 'contains_any',
         'value': ['视觉设计'], 'action': action},
        {'id': 'salary', 'field': 'salary_min', 'op': 'lt',
         'value': 5000, 'action': 'exclude'}]


def finish_lists(state):
    observe(state, 'sample', 'progress', {'tag': TAGS[0], 'batch': '001',
            'sort': '最新', 'reason': '没有更多', 'endText': '没有更多'})
    for tag in TAGS[1:]:
        def snapshot(sec):
            return {'at': stamp(sec), 'count': 0, 'cards': [],
                    'selectedTag': tag, 'selectedSort': '最新', 'endText': '暂无相关职位'}
        observe(state, 'sample', 'empty', {'tag': tag, 'batch': 'empty',
                'after1': snapshot(1), 'after2': snapshot(3)})


class CompletionTests(unittest.TestCase):
    def excluded(self):
        state = configured()
        reject_design(state)
        acquire(state, company_data=False)
        finish_lists(state)
        return state

    def test_excluded_role_closes_but_retains_unchecked_evidence(self):
        state = self.excluded()
        before = copy.deepcopy(state)
        view = results(state, 'sample')
        row = view['results'][0]
        self.assertEqual(row['status'], 'excluded')
        self.assertEqual(row['excluded'][0]['rule'], 'duties')
        self.assertTrue(row['excluded'][0]['evidence'].startswith('https://'))
        self.assertEqual(row['pending'], [])
        self.assertEqual({r['rule'] for r in row['notRequired']}, {'salary', 'evidence:company'})
        self.assertFalse(row['reviewRequired'])
        self.assertIsNone(row['companyEvidence'])
        self.assertEqual(view['missingCompanies'], [])
        self.assertTrue(view['scanComplete'])
        self.assertTrue(view['reviewComplete'])
        self.assertEqual(pending(state), [])
        self.assertEqual(state, before)

    def test_removing_exclusion_restores_unfinished_work(self):
        state = self.excluded()
        evidence = copy.deepcopy(state['runs']['sample']['evidence'])
        state['config']['rules'].pop(0)
        view = results(state, 'sample')
        row = view['results'][0]
        self.assertEqual(row['status'], 'pending')
        self.assertEqual(row['notRequired'], [])
        self.assertEqual({r['rule'] for r in row['pending']}, {'salary', 'evidence:company'})
        self.assertEqual(view['missingCompanies'], ['CC100J1001'])
        self.assertFalse(view['scanComplete'])
        self.assertFalse(view['reviewComplete'])
        self.assertTrue(pending(state))
        self.assertEqual(state['runs']['sample']['evidence'], evidence)

    def test_reminder_does_not_close_unknown_evidence(self):
        state = self.excluded()
        state['config']['rules'][0]['action'] = 'remind'
        view = results(state, 'sample')
        self.assertEqual(view['results'][0]['status'], 'pending')
        self.assertTrue(view['results'][0]['reminders'])
        self.assertFalse(view['reviewComplete'])
        self.assertTrue(pending(state))

    def test_missing_company_without_exclusion_is_still_unfinished(self):
        state = configured()
        acquire(state, company_data=False)
        finish_lists(state)
        observe(state, 'sample', 'judge', {})
        self.assertIsNone(checked_before(state, card()))
        self.assertFalse(results(state, 'sample')['scanComplete'])
        self.assertFalse(results(state, 'sample')['reviewComplete'])
        self.assertTrue(pending(state))

    def test_judged_exclusion_reused_and_invalidated_when_rule_changes(self):
        state = self.excluded()
        self.assertIsNone(checked_before(state, card()))
        observe(state, 'sample', 'judge', {})
        self.assertIsNotNone(checked_before(state, card()))
        sync_applied(state, {'source': 'https://i.zhaopin.com/', 'capturedAt': now(),
                            'total': 0, 'rows': []})
        begin(state, 'retry')
        out = observe(state, 'retry', 'cards', batch([card()]))
        self.assertFalse(out[0]['allow'])
        self.assertEqual(results(state, 'retry')['unresolvedCards'], [])
        state['config']['rules'].pop(0)
        self.assertIsNone(checked_before(state, card()))
        self.assertTrue(results(state, 'retry')['unresolvedCards'])
        self.assertTrue(pending(state))

    def test_exclusion_does_not_hide_missing_endpoints_or_click_violations(self):
        state = self.excluded()
        for alter in ('endpoint', 'violation', 'scope'):
            with self.subTest(alter=alter):
                s = copy.deepcopy(state)
                if alter == 'endpoint':
                    s['runs']['sample']['progress'].pop(TAGS[1])
                elif alter == 'violation':
                    s['runs']['sample']['violations'].append({'reason': '未登记点击'})
                else:
                    s['config']['webpage'] = {'city': '另一个城市'}
                self.assertFalse(results(s, 'sample')['scanComplete'])

    def test_excluded_role_does_not_close_other_unread_cards(self):
        state = self.excluded()
        observe(state, 'sample', 'cards', batch([card('产品经理')], name='002', previous=[card()]))
        observe(state, 'sample', 'progress', {'tag': TAGS[0], 'batch': '002',
                'sort': '最新', 'reason': '没有更多', 'endText': '没有更多'})
        view = results(state, 'sample')
        self.assertEqual(len(view['unresolvedCards']), 1)
        self.assertFalse(view['scanComplete'])
        self.assertTrue(pending(state))

    def test_review_threshold_closes_without_claiming_full_coverage(self):
        state = configured()
        state['config']['experience'] = {
            'reviews': EXPERIENCE['reviews'] | {'action': 'exclude', 'categories': ['薪资延迟'], 'threshold': 1},
            'typos': EXPERIENCE['typos'].copy()}
        acquire(state, company_data=False)
        observe(state, 'sample', 'company', company_payload(comments=['薪资结算延迟'], total=5))
        item = state['runs']['sample']['companies']['CZ100']['reviews']['comments']['items'][0]
        observe(state, 'sample', 'review', {'id': 'CC100J1001', 'reviewLabels': {
            item['key']: {'categories': ['薪资延迟'], 'quote': item['text'],
                          'source': item['source'], 'capturedAt': stamp()}}})
        finish_lists(state)
        row = results(state, 'sample')['results'][0]
        self.assertEqual(row['status'], 'excluded')
        self.assertFalse(row['excluded'][0]['value']['coverageComplete'])
        self.assertEqual(row['pending'], [])
        self.assertEqual([r['rule'] for r in row['notRequired']], ['exp:typos'])
        self.assertEqual(pending(state), [])
        state['config']['experience']['reviews']['threshold'] = 2
        self.assertFalse(results(state, 'sample')['reviewComplete'])
        self.assertTrue(pending(state))

    def with_company(self, experience, size='20人以下', comments=None, total=None):
        state = configured()
        state['config']['experience'] = experience
        acquire(state, company_data=False)
        data = company_payload(comments=comments, total=total)
        data['state']['companyDetail']['companyBase']['companySize'] = size
        observe(state, 'sample', 'company', data)
        finish_lists(state)
        return state

    def test_remind_only_experience_shows_platform_gap_without_holding_role(self):
        cases = [('hiring_structure', {'size': ''}, True),
                 ('hiring_structure', {'size': '10000人以上'}, False),
                 ('reviews', {'comments': ['环境不错'], 'total': 12}, True)]
        for name, page, gap in cases:
            with self.subTest(name=name, page=page):
                state = self.with_company({name: copy.deepcopy(EXPERIENCE[name])}, **page)
                view = results(state, 'sample')
                row = view['results'][0]
                self.assertEqual(row['status'], 'candidate')
                self.assertEqual(row['pending'], [])
                verify = [r for r in row['reminders'] if r['rule'] == 'exp:' + name and r['action'] == 'verify']
                self.assertEqual(bool(verify), gap)
                self.assertTrue(view['reviewComplete'])
                self.assertEqual(pending(state), [])
                observe(state, 'sample', 'judge', {})
                self.assertIsNotNone(checked_before(state, card()))

    def test_exclusion_with_missing_platform_data_still_waits(self):
        options = EXPERIENCE['hiring_structure'] | {'action': 'exclude'}
        state = self.with_company({'hiring_structure': options}, size='')
        row = results(state, 'sample')['results'][0]
        self.assertEqual(row['status'], 'pending')
        self.assertEqual([r['rule'] for r in row['pending']], ['exp:hiring_structure'])
        self.assertTrue(pending(state))

    def test_suppressed_roles_do_not_require_company_until_restored(self):
        for kind in ('keyword', 'rejected', 'applied'):
            with self.subTest(kind=kind):
                state = configured()
                acquire(state, company_data=False)
                finish_lists(state)
                if kind == 'keyword':
                    state['keywords'] = [{'id': 'x', 'tag': TAGS[0], 'word': '设计', 'field': 'title'}]
                else:
                    state[kind] = [{'company': '示例甲公司', 'job': '设计师'}]
                before = copy.deepcopy(state)
                view = results(state, 'sample')
                self.assertEqual(view['results'], [])
                self.assertEqual(view['missingCompanies'], [])
                self.assertTrue(view['scanComplete'])
                self.assertEqual(pending(state), [])
                self.assertEqual(state, before)
                state['keywords' if kind == 'keyword' else kind] = []
                self.assertFalse(results(state, 'sample')['scanComplete'])
                self.assertTrue(pending(state))

    def test_keyword_block_on_one_tag_does_not_close_other_visible_tag(self):
        state = configured()
        acquire(state, company_data=False)
        observe(state, 'sample', 'cards', batch([card()], TAGS[1]))
        state['keywords'] = [{'id': 'x', 'tag': TAGS[0], 'word': '设计', 'field': 'title'}]
        view = results(state, 'sample')
        self.assertEqual(view['results'][0]['visibleTags'], [TAGS[1]])
        self.assertTrue(view['missingCompanies'])
        self.assertTrue(pending(state))


if __name__ == '__main__':
    unittest.main()
