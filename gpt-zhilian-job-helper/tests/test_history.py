"""History migration stays separate from website synchronization and new applications."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from core import default_config, validate_config, effective_revision, Store
from history import import_history, reviewed_match
from repeats import repost_status
from workflow import card_judgment, results, begin, find, pending
from rules import card_fields
from zhilian import main
from test_public import configured, card, acquire, TAGS


def payload():
    return {'version': 1, 'source': 'authorized-local-export', 'seen': ['seen-only'],
            'reviewed': [{'id': 'reviewed-id', 'company': '虚构示例乙公司', 'job': '文员', 'decision': 'completed:excluded', 'completed': True}],
            'applied': [{'company': '虚构示例甲公司', 'job': '设计师'}],
            'rejected': [{'company': '虚构示例丙公司', 'job': '助理', 'reason': 'user-declined'}]}


class HistoryTests(unittest.TestCase):
    def test_repost_option_compatibility_and_validation(self):
        c = default_config()
        self.assertEqual(c['applicationPolicy'], {'reposts': 'remind', 'reuseReviewed': False})
        del c['applicationPolicy']
        self.assertIs(validate_config(c), c)
        for value in ({}, {'reposts': 'yes', 'reuseReviewed': False}, {'reposts': 'skip', 'reuseReviewed': 1}):
            with self.assertRaises(ValueError):
                validate_config(c | {'applicationPolicy': value})

    def test_skip_repost_at_first_gate_and_existing_views(self):
        s = configured()
        s['applied'] = [{'company': '虚构示例甲公司', 'job': '设计师', 'appliedAt': '2020-01-01'}]
        c = card(co='虚构示例甲公司', publishTime='2030-10-31T09:00:00+08:00')
        acquire(s, c)
        self.assertTrue(results(s, 'sample')['repostReminders'])
        s['config']['applicationPolicy']['reposts'] = 'skip'
        self.assertFalse(card_judgment(s, c, TAGS[0])['allow'])
        self.assertFalse(results(s, 'sample')['repostReminders'])
        self.assertEqual(results(s, 'sample')['results'], [])
        self.assertEqual(find(s, [{'job': '设计师'}])['matches'], [])
        self.assertTrue(card_judgment(s, card('产品经理'), TAGS[0])['allow'])

    def test_import_idempotence_and_no_fresh_sync(self):
        s = configured(); s['appliedSync'] = None
        before = effective_revision(s)
        out = import_history(s, payload())
        self.assertEqual(out['added'], {'seen': 1, 'reviewed': 1, 'applied': 1, 'rejected': 1})
        self.assertIsNone(s['appliedSync'])
        self.assertIsNone(s['applied'][0].get('appliedAt'))
        self.assertEqual(s['applied'][0]['origin'], 'local_history')
        self.assertNotEqual(before, effective_revision(s))
        saved = copy.deepcopy(s)
        self.assertTrue(import_history(s, payload())['duplicate'])
        self.assertEqual(s, saved)
        with self.assertRaises(ValueError):
            begin(s, 'new-run')

    def test_seen_does_not_skip_and_reviewed_is_opt_in(self):
        s = configured(); import_history(s, payload())
        c = card('文员', '虚构示例乙公司')
        self.assertTrue(card_judgment(s, c, TAGS[0])['allow'])
        s['config']['applicationPolicy']['reuseReviewed'] = True
        self.assertFalse(card_judgment(s, c, TAGS[0])['allow'])
        self.assertTrue(card_judgment(s, card('其他岗位', '虚构示例乙公司'), TAGS[0])['allow'])
        self.assertIsNone(reviewed_match(s, {'id': 'seen-only'}))
        s['config']['applicationPolicy']['reuseReviewed'] = False
        self.assertTrue(card_judgment(s, c, TAGS[0])['allow'])

    def test_reject_malformed_entire_payload_without_mutation(self):
        for mutate in (
            lambda p: p['applied'][0].update(message='private'),
            lambda p: p['reviewed'][0].pop('decision'),
            lambda p: p['applied'][0].pop('company'),
            lambda p: p['seen'].append({}),
            lambda p: p['applied'][0].update(appliedAt='not-a-date'),
        ):
            s = configured(); before = copy.deepcopy(s); p = payload(); mutate(p)
            with self.assertRaises(ValueError):
                import_history(s, p)
            self.assertEqual(s, before)

    def test_source_and_actual_dates_retained_without_overwriting(self):
        s = configured()
        s['applied'] = [{'company': '虚构示例甲公司', 'job': '设计师', 'appliedAt': '2021-01-01', 'status': 'keep'}]
        import_history(s, payload())
        self.assertEqual(len(s['applied']), 1)
        self.assertEqual(s['applied'][0]['status'], 'keep')
        self.assertEqual(s['applied'][0]['appliedAt'], '2021-01-01')

    def test_id_only_is_preserved_without_inventing_identity(self):
        s = configured(); p = payload()
        p['reviewed'] = [{'id': 'abc', 'decision': 'completed:excluded', 'completed': True}]
        import_history(s, p); s['config']['applicationPolicy']['reuseReviewed'] = True
        self.assertIsNotNone(reviewed_match(s, {'id': 'abc'}))
        self.assertIsNone(reviewed_match(s, {'id': 'abcd'}))
        self.assertNotIn('company', s['history']['reviewed'][0])
        s['history']['reviewed'][0]['completed'] = False
        self.assertIsNone(reviewed_match(s, {'id': 'abc'}))
        self.assertEqual(pending(s)[0]['historyReviewRequired'][0]['id'], 'abc')

    def test_imported_reviewed_suppresses_existing_pending_views(self):
        s = configured(); c = card('文员', '虚构示例乙公司')
        acquire(s, c, company_data=False)
        self.assertTrue(pending(s))
        import_history(s, payload()); s['config']['applicationPolicy']['reuseReviewed'] = True
        self.assertEqual(pending(s), [])
        self.assertEqual(results(s, 'sample')['results'], [])
        self.assertEqual(find(s, [{'job': '文员'}])['matches'], [])
        s['config']['applicationPolicy']['reuseReviewed'] = False
        self.assertTrue(pending(s))

    def test_district_and_street_are_separate_from_precise_address(self):
        c = card(cityDistrict='示例远区', streetName='示例街道')
        fields = card_fields(c)
        self.assertEqual(fields['district'], '示例远区')
        self.assertEqual(fields['street'], '示例街道')
        self.assertIsNone(fields['work_address'])
        s = configured()
        s['config']['rules'] = [{'id': 'local-area', 'field': 'district', 'op': 'contains_any', 'value': ['示例远区'], 'action': 'exclude'}]
        self.assertFalse(card_judgment(s, c, TAGS[0])['allow'])
        self.assertTrue(card_judgment(s, card(), TAGS[0])['allow'])
        self.assertTrue(card_judgment(s, card(cityDistrict='其他区'), TAGS[0])['allow'])

    def test_transaction_cli_backup_and_authorization(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'history.json'; source.write_text(json.dumps(payload()))
            project = str(Path(td) / 'user')
            main(['--project', project, 'init'])
            before = Store(project).file.read_bytes()
            with self.assertRaises(ValueError):
                main(['--project', project, 'history', 'import', '--input', str(source)])
            self.assertEqual(Store(project).file.read_bytes(), before)
            main(['--project', project, 'history', 'import', '--input', str(source), '--confirmed'])
            self.assertTrue(list((Path(project) / 'backups').glob('*.json')))
            self.assertEqual(main(['--project', project, 'history', 'summary'])['applied'], 1)


if __name__ == '__main__':
    unittest.main()
