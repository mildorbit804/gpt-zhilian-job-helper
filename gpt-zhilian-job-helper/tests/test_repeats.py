"""Applied roles published again: date reminder, user decides, history kept."""
import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from core import Store, now, effective_revision
from repeats import repost_status, annotate_history
from workflow import observe, results, card_judgment, pending, find
from zhilian import main, sync_applied
from test_public import configured, card, batch, acquire, TAGS

DATE = '2030-11-01T12:00:00+08:00'
APPLIED = '2030-07-31T12:00:00+08:00'
BEFORE = '2030-07-01T09:00:00+08:00'
AFTER = '2030-10-31T09:00:00+08:00'
ROW = {'company': '虚构示例甲公司', 'job': '设计师'}


def posting(published=AFTER, name=ROW['job'], co=ROW['company']):
    return card(name, co, publishTime=published)


def with_history(record=None):
    s = configured()
    s['applied'] = [ROW | (record or {'appliedAt': APPLIED})]
    return s


class RepostTests(unittest.TestCase):
    def test_posting_after_application_is_let_through_with_date(self):
        s = with_history()
        self.assertTrue(card_judgment(s, posting(), TAGS[0])['allow'])
        acquire(s, posting())
        view = results(s, 'sample')
        self.assertEqual(len(view['results']), 1)
        self.assertEqual(view['repostReminders'][0]['repost']['appliedAt'], '2030-07-31')
        self.assertIn('2030-07-31 投过', view['repostReminders'][0]['repost']['reminder'])
        self.assertEqual(view['unresolvedCards'], [])
        self.assertEqual(view['missingCompanies'], [])

    def test_same_posting_or_unknown_publish_time_stays_skipped(self):
        s = with_history()
        for published in (BEFORE, APPLIED, None):
            with self.subTest(published=published):
                judgment = card_judgment(s, card(ROW['job'], ROW['company'], **({'publishTime': published} if published else {})), TAGS[0])
                self.assertFalse(judgment['allow'])
                self.assertEqual(judgment['reason'], '已投同公司同岗位')

    def test_bare_page_date_covers_the_whole_day(self):
        s = with_history({'appliedAt': '2030-10-31'})
        self.assertFalse(card_judgment(s, posting('2030-10-31T22:00:00+08:00'), TAGS[0])['allow'])
        self.assertTrue(card_judgment(s, posting('2030-11-01T08:00:00+08:00'), TAGS[0])['allow'])

    def test_undated_history_uses_import_time_as_bound(self):
        s = with_history({'appliedAt': None, 'importedAt': '2030-08-15T10:00:00+08:00'})
        self.assertFalse(card_judgment(s, posting('2030-08-01T10:00:00+08:00'), TAGS[0])['allow'])
        status = repost_status(s, ROW, AFTER)
        self.assertEqual(status['status'], 'repost')
        self.assertIsNone(status['appliedAt'])
        self.assertIn('日期没记录，在 2030-08-15 之前', status['reminder'])
        s['applied'][0].pop('importedAt')
        self.assertEqual(repost_status(s, ROW, AFTER)['status'], 'applied')

    def test_repost_still_needs_every_user_rule(self):
        s = with_history(); acquire(s, posting())
        self.assertEqual(len(results(s, 'sample')['repostReminders']), 1)
        s['config']['rules'] = [{'id': 'rule', 'field': 'title', 'op': 'contains_any', 'value': ['设计师'], 'action': 'exclude'}]
        self.assertFalse(card_judgment(s, posting(), TAGS[0])['allow'])
        self.assertEqual(results(s, 'sample')['results'][0]['status'], 'excluded')
        self.assertEqual(results(s, 'sample')['repostReminders'], [])

    def test_declining_records_only_this_role(self):
        s = with_history(); acquire(s, posting())
        s['rejected'].append(ROW | {'reason': '投过的岗位重新发布，使用者选择不投'})
        view = results(s, 'sample')
        self.assertEqual(view['repostReminders'], [])
        self.assertEqual(view['suppressed'][0]['suppressedReason'], '用户已拒绝该岗位')
        self.assertTrue(card_judgment(s, posting(name='产品经理'), TAGS[0])['allow'])

    def test_applying_again_ends_the_reminder_and_keeps_history(self):
        s = with_history(); acquire(s, posting()); rev = effective_revision(s)
        s['applied'][0]['appliedAt'] = '2030-11-01T10:00:00+08:00'
        self.assertNotEqual(effective_revision(s), rev)
        self.assertEqual(results(s, 'sample')['repostReminders'], [])
        self.assertEqual(results(s, 'sample')['suppressed'][0]['suppressedReason'], '已投同公司同岗位')
        self.assertEqual(len(s['applied']), 1)

    def test_find_and_pending_share_the_view(self):
        s = with_history(); acquire(s, posting())
        match = find(s, [{'job': '设计师'}])['matches'][0]
        self.assertEqual(match['repost']['status'], 'repost')
        self.assertFalse(any('repeatCandidates' in p for p in pending(s)))

    @patch('repeats.now', return_value=DATE)
    def test_sync_reads_page_dates_without_rewinding(self, clock):
        s = configured()
        captured = '2030-11-01T10:00:00+08:00'
        rows = [ROW | {'appliedAt': '2030-07-31'}, {'company': '虚构示例乙公司', 'job': '文员'}]
        out = sync_applied(s, {'source': 'https://i.zhaopin.com/', 'capturedAt': captured, 'total': 2, 'rows': rows})
        self.assertEqual(out['added'], 2)
        self.assertEqual(s['applied'][0]['appliedAt'], '2030-07-31')
        self.assertIsNone(s['applied'][1]['appliedAt'])
        later = [ROW | {'appliedAt': '2030-06-01'}, {'company': '虚构示例乙公司', 'job': '文员', 'appliedAt': '2030-09-01'}]
        out = sync_applied(s, {'source': 'https://i.zhaopin.com/', 'capturedAt': captured, 'total': 2, 'rows': later})
        self.assertEqual(out['datesFilled'], 1)
        self.assertEqual(s['applied'][0]['appliedAt'], '2030-07-31')
        self.assertEqual(s['applied'][1]['appliedAt'], '2030-09-01')
        for bad in [ROW | {'appliedAt': '2030-11-02'}, ROW | {'appliedAt': '7月31日'}, ROW | {'outcome': 'no_reply'}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                sync_applied(s, {'source': 'https://i.zhaopin.com/', 'capturedAt': captured, 'total': 1, 'rows': [bad]})

    @patch('repeats.now', return_value=DATE)
    def test_annotate_only_fills_dates(self, clock):
        s = with_history({'appliedAt': None})
        annotate_history(s, ROW | {'appliedAt': '2030-07-31'})
        self.assertEqual(s['applied'][0]['dateSource'], 'user_confirmed')
        for bad in [ROW | {'appliedAt': '2030-06-01'}, ROW | {'appliedAt': APPLIED, 'outcome': 'rejected'},
                    {'company': '不存在的公司', 'job': '设计师', 'appliedAt': APPLIED}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                annotate_history(s, bad)

    @patch('repeats.now', return_value=DATE)
    def test_undated_reapplication_keeps_old_date_and_ends_reminder(self, clock):
        with tempfile.TemporaryDirectory() as tmp:
            base = ['--project', tmp]; main(base + ['init'])
            data = Path(tmp) / 'input.json'
            def run(*args, payload):
                data.write_text(json.dumps(payload))
                main(base + list(args) + ['--input', str(data), '--confirmed'])
            def page(captured, applied):
                return {'source': 'https://i.zhaopin.com/', 'capturedAt': captured, 'total': 1, 'rows': [ROW | {'appliedAt': applied}]}
            run('applied', 'sync', payload=page('2030-08-15T10:00:00+08:00', '2030-07-31'))
            self.assertEqual(repost_status(Store(tmp).read(), ROW, AFTER)['status'], 'repost')
            run('applied', 'add', payload=ROW)
            record = Store(tmp).read()['applied'][0]
            self.assertIsNone(record['appliedAt'])
            self.assertEqual(record['earlier'][0]['appliedAt'], '2030-07-31')
            self.assertEqual(repost_status(Store(tmp).read(), ROW, AFTER)['status'], 'applied')
            run('applied', 'sync', payload=page('2030-11-01T11:00:00+08:00', '2030-07-31'))
            self.assertIsNone(Store(tmp).read()['applied'][0]['appliedAt'])
            self.assertEqual(repost_status(Store(tmp).read(), ROW, AFTER)['status'], 'applied')
            run('applied', 'sync', payload=page('2030-11-01T11:00:00+08:00', '2030-11-01'))
            later = repost_status(Store(tmp).read(), ROW, '2030-11-05T09:00:00+08:00')
            self.assertEqual((later['status'], later['appliedAt']), ('repost', '2030-11-01'))

    def test_cli_confirmation_and_reminder_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = ['--project', tmp]; main(base + ['init'])
            data = Path(tmp) / 'input.json'
            past = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=150)).isoformat()
            data.write_text(json.dumps(ROW | {'appliedAt': past}))
            before = (Path(tmp) / 'zhilian-state.json').read_bytes()
            with self.assertRaises(ValueError): main(base + ['applied', 'add', '--input', str(data)])
            self.assertEqual((Path(tmp) / 'zhilian-state.json').read_bytes(), before)
            main(base + ['applied', 'add', '--input', str(data), '--confirmed'])
            self.assertEqual(Store(tmp).read()['applied'][0]['appliedAt'], past)
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                main(base + ['repeat', 'decide', '--run', 'x'])


if __name__ == '__main__':
    unittest.main()
