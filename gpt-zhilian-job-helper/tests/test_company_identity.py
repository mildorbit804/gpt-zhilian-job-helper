"""Observed employer identity, including job/company numbers with different prefixes."""
import copy
import unittest

from test_public import (configured, card, batch, TAGS, stamp,
                         detail_payload, company_payload)
from workflow import observe, results, pending


class CompanyIdentityTests(unittest.TestCase):
    def selected(self, row=None):
        state = configured()
        row = row or card()
        key = observe(state, 'sample', 'cards', batch([row]))[0]['key']
        observe(state, 'sample', 'select', {'tag': TAGS[0], 'batch': '001', 'keys': [key], 'rows': [row]})
        observe(state, 'sample', 'clickbatch', {'tag': TAGS[0], 'batch': '001',
                'items': [{'key': key, 'heading': row['name'], 'id': 'CC100J1001'}]})
        payload = detail_payload(row, key)
        return state, payload

    def test_explicit_company_number_overrides_job_prefix(self):
        state, payload = self.selected()
        payload['state']['jobDetail']['detailedCompany']['companyNumber'] = 'CZ101'
        record = observe(state, 'sample', 'detail', payload)
        self.assertEqual(record['companyNumber'], 'CZ101')
        self.assertEqual(record['companyNumberEvidence'][0]['value'], 'CZ101')
        observe(state, 'sample', 'company', company_payload('CZ101'))
        self.assertEqual(results(state, 'sample')['missingCompanies'], [])
        self.assertEqual(pending(state), [])
        self.assertEqual(results(state, 'sample')['results'][0]['companyEvidence']['source'],
                         'https://www.zhaopin.com/companydetail/CZ101.htm')

    def test_observed_link_can_supply_missing_number(self):
        for where in ('card', 'detail'):
            with self.subTest(where=where):
                url = 'https://www.zhaopin.com/companydetail/CZL101.htm?from=job'
                state, payload = self.selected(card(companyUrl=url) if where == 'card' else None)
                payload['state']['jobDetail']['detailedCompany'].pop('companyNumber')
                if where == 'detail': payload['companyUrl'] = url
                record = observe(state, 'sample', 'detail', payload)
                self.assertEqual(record['companyNumber'], 'CZL101')
                self.assertEqual(record['companyNumberEvidence'][0]['value'], url)
                observe(state, 'sample', 'company', company_payload('CZL101'))
                self.assertEqual(pending(state), [])

    def test_absent_number_and_link_never_guesses_from_job(self):
        state, payload = self.selected()
        payload['state']['jobDetail']['detailedCompany'].pop('companyNumber')
        record = observe(state, 'sample', 'detail', payload)
        self.assertIsNone(record['companyNumber'])
        self.assertEqual(record['companyNumberEvidence'], [])
        self.assertTrue(pending(state))
        before = copy.deepcopy(state)
        with self.assertRaises(ValueError): observe(state, 'sample', 'company', company_payload())
        self.assertEqual(state, before)

    def test_conflicting_number_and_observed_links_do_not_bind(self):
        for where in ('card', 'detail'):
            with self.subTest(where=where):
                url = 'https://www.zhaopin.com/companydetail/CZ101.htm'
                state, payload = self.selected(card(companyUrl=url) if where == 'card' else None)
                if where == 'detail': payload['companyUrl'] = url
                before = copy.deepcopy(state)
                with self.assertRaises(ValueError): observe(state, 'sample', 'detail', payload)
                self.assertEqual(state, before)

    def test_invalid_number_is_not_accepted_as_identity(self):
        for value in ('CC100', 'CZ100/other', 100, {}, ['CZ100']):
            with self.subTest(value=value):
                state, payload = self.selected()
                payload['state']['jobDetail']['detailedCompany']['companyNumber'] = value
                with self.assertRaises(ValueError): observe(state, 'sample', 'detail', payload)

    def test_company_link_requires_exact_path_and_trusted_https(self):
        for url in ('https://example.com/companydetail/CZ100.htm',
                    'https://www.zhaopin.com.example.com/companydetail/CZ100.htm',
                    'http://www.zhaopin.com/companydetail/CZ100.htm',
                    'https://user' + '@www.zhaopin.com/companydetail/CZ100.htm',  # URL carrying a username
                    'https://www.zhaopin.com/other?company=CZ100',
                    'https://www.zhaopin.com/companydetail/CZ100.htm/other'):
            with self.subTest(url=url):
                state, payload = self.selected()
                payload['companyUrl'] = url
                with self.assertRaises(ValueError): observe(state, 'sample', 'detail', payload)

    def test_company_urls_cannot_match_only_in_query(self):
        for key, url in [('url', 'https://www.zhaopin.com/companydetail/CZ999.htm?other=CZ100'),
                         ('jobsUrl', 'https://www.zhaopin.com/companydetail/jobs-CZ999/?other=CZ100')]:
            with self.subTest(key=key):
                state, payload = self.selected(); observe(state, 'sample', 'detail', payload)
                co = company_payload(); co[key] = url
                before = copy.deepcopy(state)
                with self.assertRaises(ValueError): observe(state, 'sample', 'company', co)
                self.assertEqual(state, before)

    def test_conflicting_homepage_identity_is_rejected(self):
        for field, value in [('companyNumber', 'CZ999'), ('companyName', '另一家示例公司')]:
            with self.subTest(field=field):
                state, payload = self.selected(); observe(state, 'sample', 'detail', payload)
                co = company_payload(); co['state']['companyDetail']['companyBase'][field] = value
                with self.assertRaises(ValueError): observe(state, 'sample', 'company', co)

    def test_confirmed_company_alias_is_allowed(self):
        state, payload = self.selected(); observe(state, 'sample', 'detail', payload)
        state['config']['companyAliases'] = [['示例甲公司', '示例设计品牌']]
        co = company_payload(); co['state']['companyDetail']['companyBase']['companyName'] = '示例设计品牌'
        observe(state, 'sample', 'company', co)
        self.assertEqual(pending(state), [])

    def test_legacy_guessed_number_needs_detail_evidence_before_new_company_import(self):
        state, payload = self.selected(); observe(state, 'sample', 'detail', payload)
        state['runs']['sample']['details']['CC100J1001'].pop('companyNumberEvidence', None)
        before = copy.deepcopy(state)
        with self.assertRaises(ValueError): observe(state, 'sample', 'company', company_payload())
        self.assertEqual(state, before)
        observe(state, 'sample', 'detail', payload)
        observe(state, 'sample', 'company', company_payload())
        self.assertEqual(pending(state), [])

    def test_new_identity_preserves_old_detail_evidence(self):
        state, payload = self.selected(); observe(state, 'sample', 'detail', payload)
        old = copy.deepcopy(state['runs']['sample']['evidence'])
        payload['state']['jobDetail']['detailedCompany']['companyNumber'] = 'CZ101'
        payload['capturedAt'] = stamp(6)
        observe(state, 'sample', 'detail', payload)
        self.assertEqual(state['runs']['sample']['evidence'][:len(old)], old)
        self.assertEqual(state['runs']['sample']['details']['CC100J1001']['companyNumber'], 'CZ101')


if __name__ == '__main__': unittest.main()
