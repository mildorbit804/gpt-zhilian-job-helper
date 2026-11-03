"""Synthetic tests only. No browser, personal logs, accounts or network."""
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from core import Store, EXPERIENCE, new_state, default_config, validate_config, identity, same, now, timestamp, effective_revision
from rules import evaluate, capital, work_times, normalize_company, keyword_matches
from workflow import begin, observe, results, pending, find
from zhilian import main, sync_applied


TAGS = ['设计师', '数据分析师', '产品经理']
T0 = dt.datetime(2035, 6, 15, 12, tzinfo=dt.timezone.utc)


def stamp(seconds=0):
    return (T0 + dt.timedelta(seconds=seconds)).isoformat()


def configured():
    state = new_state()
    state['config']['tags'] = TAGS[:]
    sync_applied(state, {'source': 'https://i.zhaopin.com/', 'capturedAt': now(), 'total': 0, 'rows': []})
    begin(state, 'sample')
    return state


def card(name='设计师', co='示例甲公司', **extra):
    return {'name': name, 'companyName': co, 'industryName': '示例行业', **extra}


def batch(rows, tag=TAGS[0], name='001', previous=None):
    previous = previous or [card('旧标签岗位', '虚构旧公司')]
    mode = 'more' if name != '001' else 'switch'
    final = previous + rows if mode == 'more' else rows
    def evidence(values, at):
        return {'cards': copy.deepcopy(values), 'count': len(values), 'first3': copy.deepcopy(values[:3]),
                'at': stamp(at), 'selectedTag': tag, 'selectedSort': '最新'}
    return {'tag': tag, 'batch': name, 'title': '智联职位', 'sort': '最新', 'rows': copy.deepcopy(rows),
            'listEvidence': {'mode': mode, 'before': evidence(previous, 0),
                             'after1': evidence(final, 1), 'after2': evidence(final, 3)}}


def detail_payload(row, k, ident='CC100J1001', tag=TAGS[0], desc='负责视觉设计，熟恋使用软件。08:30-18:00'):
    return {'tag': tag, 'batch': '001', 'key': k, 'url': f'https://www.zhaopin.com/jobdetail/{ident}.htm',
            'capturedAt': stamp(), 'state': {'jobNumber': ident, 'privateChat': 'must-not-persist',
                'jobDetail': {'detailedPosition': {'number': ident, 'name': row['name'], 'jobDesc': desc, 'workAddress': '示例园区A座',
                                                   'hasAppliedPosition': 'private-status'},
                              'detailedCompany': {'companyName': row['companyName'],
                                  'companyNumber': ident.split('J')[0].replace('CC', 'CZ', 1),
                                  'companyDescription': '设计服务', 'industryNameLevel': row['industryName']}}}}


def company_payload(cn='CZ100', cap='50万元人民币', work='08:30~18:00', comments=None, total=None, offset=0):
    comments = [] if comments is None else comments
    return {'companyNumber': cn, 'capturedAt': stamp(offset), 'url': f'https://www.zhaopin.com/companydetail/{cn}.htm',
            'jobsUrl': f'https://www.zhaopin.com/companydetail/jobs-{cn}/',
            'state': {'companyCode': cn, 'companyDetail': {'workTimeTag': {'workTime': work, 'breakTimeTag': '双休'},
                'companyComment': {'reviewsSum': len(comments) if total is None else total, 'items': [{'content': c} for c in comments]},
                'companyInterview': {'total': 0, 'items': []},
                'businessInformationData': {'businessInformationData': {'registeredCapital': cap, 'location': '示例园区', 'epType': '有限公司'}},
                'companyBase': {'companySize': '20人以下'}}},
            'jobs': {'companyCode': cn, 'onlinePositionsCount': 1, 'onlinePositions': [{'name': '设计师'}]}}


def acquire(state, row=None, ident='CC100J1001', tag=TAGS[0], company_data=True):
    row = row or card()
    out = observe(state, 'sample', 'cards', batch([row], tag))
    k = out[0]['key']
    observe(state, 'sample', 'select', {'tag': tag, 'batch': '001', 'keys': [k], 'rows': [row]})
    observe(state, 'sample', 'clickbatch', {'tag': tag, 'batch': '001', 'items': [{'key': k, 'heading': row['name'], 'id': ident}]})
    observe(state, 'sample', 'detail', detail_payload(row, k, ident, tag))
    if company_data:
        cn = ident.split('J')[0].replace('CC', 'CZ')
        observe(state, 'sample', 'company', company_payload(cn))
    return k


def final_decision(state):
    return results(state, 'sample')['results'][0]


class ConfigurationTests(unittest.TestCase):
    def test_blank_defaults(self):
        c = default_config()
        self.assertEqual(c['rules'], [])
        self.assertEqual(c['experience'], {})
        self.assertEqual(c['tags'], [])
        self.assertIsNone(c['scan']['days'])
        validate_config(c)

    def test_bad_config_rejected(self):
        for change in [lambda c: c.update(extra=True), lambda c: c['scan'].update(days=True),
                       lambda c: c['scan'].update(days=-1), lambda c: c['experience'].update(span={'action':'exclude','threshold':25}),
                       lambda c: c['experience'].update(capital={'action':'exclude','threshold':float('nan')}),
                       lambda c: c['experience'].update(reviews={'action':'exclude','categories':[],'threshold':1,'mode':'combined'})]:
            with self.subTest(change=change):
                c = default_config(); change(c)
                with self.assertRaises(ValueError): validate_config(c)

    def test_identity_no_city_or_entire_company_expansion(self):
        cfg = default_config()
        self.assertTrue(same({'company':'示例甲有限公司','job':'人事行政专员(J123)'}, {'company':'示例甲','job':'行政人事专员'}, cfg))
        self.assertFalse(same({'company':'示例甲','job':'设计师'}, {'company':'示例甲','job':'产品经理'}, cfg))
        self.assertFalse(same({'company':'示例甲','job':'设计师'}, {'company':'新区示例甲','job':'设计师'}, cfg))
        cfg['companyAliases'] = [['示例甲', '新区示例甲']]
        self.assertTrue(same({'company':'示例甲','job':'设计师'}, {'company':'新区示例甲','job':'设计师'}, cfg))

    def test_no_personal_filter(self):
        s = configured()
        for n in ['轮班技术员', '兼职编辑', '实习设计师']:
            row = {'company': '任意企业', 'job': n, 'fields': {'title': n, 'industry': '任意行业', 'work_address': '任意地区'}}
            self.assertFalse(evaluate(row, s, stage='card')['excluded'])

    def test_user_rule_applies_only_when_enabled(self):
        s = configured()
        acquire(s)
        rule = {'id':'my-industry','field':'industry','op':'contains_any','value':['示例行业'],'action':'exclude'}
        self.assertEqual(final_decision(s)['status'], 'candidate')
        s['config']['rules'] = [rule]
        self.assertEqual(final_decision(s)['status'], 'excluded')
        rule['action'] = 'remind'
        self.assertEqual(final_decision(s)['status'], 'candidate')
        s['config']['rules'] = []
        self.assertFalse(final_decision(s)['reminders'])


class ExperienceTests(unittest.TestCase):
    def setUp(self):
        self.s = configured(); acquire(self.s)

    def enable(self, name, **options):
        self.s['config']['experience'][name] = EXPERIENCE[name] | options

    def test_capital_boundary_and_modified_threshold(self):
        self.enable('capital')
        self.assertEqual(final_decision(self.s)['status'], 'excluded')
        self.enable('capital', threshold=499999)
        self.assertEqual(final_decision(self.s)['status'], 'candidate')
        self.enable('capital', threshold=500000, action='remind')
        self.assertEqual(final_decision(self.s)['status'], 'candidate')
        self.assertTrue(final_decision(self.s)['reminders'])

    def test_capital_units_currency_missing(self):
        self.assertEqual(capital('人民币0.005亿元'), 500000)
        self.assertIsNone(capital('50万美元'))
        self.assertIsNone(capital('50万元'))
        self.enable('capital')
        self.s['runs']['sample']['companies']['CZ100']['capitalText'] = ''
        self.assertEqual(final_decision(self.s)['status'], 'pending')

    def test_span_boundary(self):
        self.enable('span')
        self.assertEqual(final_decision(self.s)['status'], 'excluded')
        self.enable('span', threshold=9.5)
        self.assertEqual(final_decision(self.s)['status'], 'candidate')
        self.enable('span', threshold=10)
        self.assertEqual(final_decision(self.s)['status'], 'candidate')

    def test_no_invented_net_hours(self):
        self.assertEqual(work_times('22:00-06:00'), {})
        self.s['config']['rules'] = [{'id':'net','field':'net_work_hours','op':'gt','value':8,'action':'exclude'}]
        self.assertEqual(final_decision(self.s)['status'], 'pending')

    def test_remove_restores_without_erasing_evidence(self):
        before = copy.deepcopy(self.s['runs']['sample']['evidence'])
        self.enable('capital'); self.assertEqual(final_decision(self.s)['status'], 'excluded')
        self.s['config']['experience'] = {}
        self.assertEqual(final_decision(self.s)['status'], 'candidate')
        self.assertEqual(before, self.s['runs']['sample']['evidence'])
        self.assertEqual(self.s['rejected'], [])

    def test_typos_need_actual_quote(self):
        self.enable('typos')
        self.assertEqual(final_decision(self.s)['status'], 'pending')
        fact = {'verdict':'match', 'quote':'熟恋', 'correction':'熟练', 'source':'https://www.zhaopin.com/jobdetail/CC100J1001.htm', 'capturedAt':stamp()}
        observe(self.s, 'sample', 'review', {'id':'CC100J1001','checks':{'exp:typos':fact}})
        self.assertEqual(final_decision(self.s)['status'], 'excluded')
        fact['quote'] = '不存在的原文'
        with self.assertRaises(ValueError): observe(self.s,'sample','review',{'id':'CC100J1001','checks':{'exp:typos':fact}})

    def test_reviews_not_zero_when_incomplete(self):
        self.enable('reviews', action='exclude', categories=['薪资延迟'])
        observe(self.s, 'sample', 'company', company_payload(comments=[], total=5))
        self.assertEqual(final_decision(self.s)['status'], 'pending')

    def test_reviews_threshold_labels_and_duplicate_count(self):
        self.enable('reviews', action='exclude', categories=['薪资延迟'], threshold=2)
        observe(self.s,'sample','company',company_payload(comments=['薪资结算延迟','薪资结算延迟'],total=2))
        co = self.s['runs']['sample']['companies']['CZ100']
        item = co['reviews']['comments']['items'][0]
        label = {'categories':['薪资延迟'],'quote':item['text'],'source':item['source'],'capturedAt':stamp()}
        observe(self.s,'sample','review',{'id':'CC100J1001','reviewLabels':{item['key']:label}})
        self.assertEqual(final_decision(self.s)['status'], 'pending')
        self.enable('reviews', action='exclude', categories=['薪资延迟'], threshold=1)
        decision = final_decision(self.s)
        self.assertEqual(decision['status'],'excluded')
        self.assertEqual(decision['excluded'][0]['value']['uniqueNegativeCount'],1)
        self.assertFalse(decision['excluded'][0]['value']['coverageComplete'])

    def test_manual_fact_requires_source(self):
        with self.assertRaises(ValueError):
            observe(self.s,'sample','review',{'id':'CC100J1001','facts':{'salary_min':{'value':5000}}})
        value = {'value':5000,'quote':'每月5000元','source':'https://www.zhaopin.com/jobdetail/CC100J1001.htm','capturedAt':stamp()}
        observe(self.s,'sample','review',{'id':'CC100J1001','facts':{'salary_min':value}})
        self.assertEqual(final_decision(self.s)['fields']['salary_min'],5000)

    def test_negative_overtime_phrase_does_not_exclude(self):
        self.enable('mandatory_overtime')
        record=self.s['runs']['sample']['details']['CC100J1001']
        record['fields']['description']='本岗位并非必须接受加班。'
        self.assertEqual(final_decision(self.s)['status'],'candidate')
        record['fields']['description']='必须接受加班。'
        self.assertEqual(final_decision(self.s)['status'],'excluded')

    def test_rest_conflict_requires_verification(self):
        record=self.s['runs']['sample']['details']['CC100J1001']
        record['fields']['description']='每周单休。'
        self.s['config']['rules']=[{'id':'rest','field':'rest_schedule','op':'not_contains_any','value':['双休'],'action':'exclude'}]
        decision=final_decision(self.s)
        self.assertEqual(decision['status'],'pending')
        self.assertTrue(any(r['rule']=='evidence:rest_conflict' for r in decision['reminders']))

    def test_company_evidence_visible_without_experience(self):
        observe(self.s,'sample','company',company_payload(comments=['虚构评价原文']))
        decision=final_decision(self.s)
        self.assertEqual(decision['companyEvidence']['reviews']['comments']['items'][0]['text'],'虚构评价原文')
        self.assertEqual(decision['status'],'candidate')


class WorkflowTests(unittest.TestCase):
    def test_empty_tags_require_positive_evidence(self):
        s=configured()
        def empty(tag):
            def snap(sec):return {'at':stamp(sec),'count':0,'cards':[],'selectedTag':tag,'selectedSort':'最新','endText':'暂无相关职位'}
            return {'tag':tag,'batch':'empty','after1':snap(1),'after2':snap(3)}
        bad=empty(TAGS[0]);bad['after2']['endText']='加载中'
        with self.assertRaises(ValueError):observe(s,'sample','empty',bad)
        for tag in TAGS:observe(s,'sample','empty',empty(tag))
        self.assertTrue(results(s,'sample')['scanComplete'])
        s['config']['webpage']={'city':'使用者选择的另一个城市'}
        self.assertFalse(results(s,'sample')['scanComplete'])
        self.assertTrue(results(s,'sample')['scopeChanged'])

    def test_stale_applied_list_cannot_start_scan(self):
        s=new_state();s['config']['tags']=TAGS[:]
        sync_applied(s,{'source':'https://i.zhaopin.com/','capturedAt':'2001-01-01T00:00:00+00:00','total':0,'rows':[]})
        with self.assertRaises(ValueError):begin(s,'sample')

    def test_new_page_invalidates_old_endpoint(self):
        s=configured();row=card();observe(s,'sample','cards',batch([row]))
        proof={'tag':TAGS[0],'batch':'001','sort':'最新','reason':'没有更多','endText':'没有更多'}
        observe(s,'sample','progress',proof)
        observe(s,'sample','cards',batch([card('新岗位')],name='002',previous=[row]))
        self.assertNotIn(TAGS[0],s['runs']['sample']['progress'])
        with self.assertRaises(ValueError):observe(s,'sample','progress',proof)

    def test_list_payload_does_not_store_unrelated_data(self):
        s=configured();payload=batch([card(secret='private-list-value')])
        observe(s,'sample','cards',payload)
        self.assertNotIn('private-list-value',json.dumps(s))

    def test_company_mismatch_and_spacing(self):
        s=configured();acquire(s)
        bad=company_payload();bad['jobs']['companyCode']='CZ999'
        with self.assertRaises(ValueError):observe(s,'sample','company',bad)
        acquire(s,card('数据分析师','示例乙公司'),'CC200J2001',TAGS[1],False)
        with self.assertRaises(ValueError):observe(s,'sample','company',company_payload('CZ200',offset=4))
        observe(s,'sample','company',company_payload('CZ200',offset=5))

    def test_successful_retry_resolves_old_pending_index(self):
        s=configured();acquire(s,company_data=False)
        self.assertTrue(pending(s))
        sync_applied(s,{'source':'https://i.zhaopin.com/','capturedAt':now(),'total':0,'rows':[]});begin(s,'retry')
        # Populate the retry through the same observation channel, not an inferred completion marker.
        s['runs']['old']=s['runs'].pop('sample');s['runs']['sample']=s['runs'].pop('retry')
        acquire(s);observe(s,'sample','judge',{})
        self.assertEqual(pending(s),[])

    def test_confirmed_alias_can_be_used_for_find(self):
        s=configured();acquire(s)
        s['config']['companyAliases']=[['示例甲公司','示例设计品牌']]
        self.assertEqual(len(find(s,[{'company':'示例设计品牌','job':'设计'}])['matches']),1)

    def test_applied_sync_and_repost(self):
        s = new_state(); s['config']['tags'] = TAGS[:]
        with self.assertRaises(ValueError): begin(s,'x')
        payload={'source':'https://i.zhaopin.com/','capturedAt':now(),'total':2,
                 'rows':[{'company':'示例甲有限公司','job':'设计师(J123)'},{'company':'示例甲','job':'设计师'}]}
        self.assertEqual(sync_applied(s,payload)['added'],1)
        self.assertIsNone(s['applied'][0]['appliedAt'])
        begin(s,'sample')
        out=observe(s,'sample','cards',batch([card(co='示例甲'),card(name='产品经理',co='示例甲')]))
        self.assertFalse(out[0]['allow']);self.assertTrue(out[1]['allow'])
        with self.assertRaises(ValueError):begin(s,'second')

    def test_sync_bad_total_and_unnecessary_data(self):
        s=new_state()
        for rows,total in [([],1),([{'company':'X','job':'Y','message':'private'}],1)]:
            with self.assertRaises(ValueError):sync_applied(s,{'source':'https://i.zhaopin.com/','capturedAt':now(),'rows':rows,'total':total})
        self.assertEqual(s['applied'],[])

    def test_unstable_list_is_rejected(self):
        s=configured(); payload=batch([card()]);payload['listEvidence']['after2']['at']=stamp(1)
        with self.assertRaises(ValueError):observe(s,'sample','cards',payload)
        self.assertEqual(s['runs']['sample']['cards'],{})

    def test_more_requires_prefix_unchanged(self):
        s=configured(); rows=[card()]
        observe(s,'sample','cards',batch(rows))
        bad=batch([card('产品经理')],name='002',previous=[card('错误前缀')])
        with self.assertRaises(ValueError):observe(s,'sample','cards',bad)

    def test_duplicate_first_card_only(self):
        s=configured(); rows=[card(),card()]
        out=observe(s,'sample','cards',batch(rows))
        self.assertEqual([r['allow'] for r in out],[True,False])
        selected=observe(s,'sample','select',{'tag':TAGS[0],'batch':'001','keys':[out[0]['key']],'rows':rows})
        self.assertEqual(selected[0]['index'],0)
        self.assertEqual(selected[0]['matches'],2)

    def test_selection_atomic(self):
        s=configured(); rows=[card(),card('产品经理','示例乙公司')]
        out=observe(s,'sample','cards',batch(rows))
        with self.assertRaises(ValueError): observe(s,'sample','select',{'tag':TAGS[0],'batch':'001','keys':[r['key'] for r in out],'rows':rows[:1]})
        self.assertFalse(any('selection' in e for e in s['runs']['sample']['cards'].values()))

    def test_unselected_click_violation(self):
        s=configured();out=observe(s,'sample','cards',batch([card()]))
        response=observe(s,'sample','clickbatch',{'tag':TAGS[0],'batch':'001','items':[{'key':out[0]['key'],'id':'CC100J1001','heading':'设计师'}]})
        self.assertFalse(response['ok']);self.assertTrue(s['runs']['sample']['violations'])

    def test_wrong_heading_then_identify(self):
        s=configured();out=observe(s,'sample','cards',batch([card()]));k=out[0]['key']
        observe(s,'sample','select',{'tag':TAGS[0],'batch':'001','keys':[k],'rows':[card()]})
        response=observe(s,'sample','clickbatch',{'tag':TAGS[0],'batch':'001','items':[{'key':k,'id':'CC100J1001','heading':'旧岗位'}]})
        self.assertFalse(response['ok'])
        self.assertTrue(observe(s,'sample','identify',{'tag':TAGS[0],'batch':'001','key':k,'id':'CC100J1001','heading':'设计师'})['ok'])

    def test_only_detail_never_completed(self):
        s=configured();acquire(s,company_data=False)
        self.assertTrue(pending(s));self.assertFalse(results(s,'sample')['scanComplete'])
        observe(s,'sample','judge',{})
        sync_applied(s,{'source':'https://i.zhaopin.com/','capturedAt':now(),'total':0,'rows':[]});begin(s,'retry')
        out=observe(s,'retry','cards',batch([card()]))
        self.assertTrue(out[0]['allow'])

    def test_completed_cache_invalidated_on_rule_change(self):
        s=configured();acquire(s);observe(s,'sample','judge',{})
        sync_applied(s,{'source':'https://i.zhaopin.com/','capturedAt':now(),'total':0,'rows':[]});begin(s,'retry')
        self.assertFalse(observe(s,'retry','cards',batch([card()]))[0]['allow'])
        s['config']['experience']['capital']=EXPERIENCE['capital'].copy()
        self.assertTrue(results(s,'retry')['unresolvedCards'])

    def test_personal_page_fields_not_persisted(self):
        s=configured();acquire(s)
        serial=json.dumps(s,ensure_ascii=False)
        self.assertNotIn('must-not-persist',serial);self.assertNotIn('private-status',serial)

    def test_all_tag_endpoints_needed(self):
        s=configured();acquire(s)
        def end(tag):return observe(s,'sample','progress',{'tag':tag,'batch':'001','sort':'最新','reason':'没有更多','endText':'没有更多'})
        end(TAGS[0]);self.assertFalse(results(s,'sample')['scanComplete'])
        for i,tag in enumerate(TAGS[1:],2):
            acquire(s,card(tag,'示例'+str(i)+'公司'),f'CC{100+i}J{1000+i}',tag,company_data=False)
            observe(s,'sample','company',company_payload(f'CZ{100+i}',offset=i*6))
            end(tag)
        self.assertTrue(results(s,'sample')['scanComplete'])

    def test_time_window_custom_and_no_unproven_end(self):
        s=configured();s['config']['scan']['days']=3;s['runs']['sample']['scope']['scan']['days']=3
        rows=[card(str(i)+'设计师',publishTime=(T0-dt.timedelta(days=5)).isoformat()) for i in range(20)]
        observe(s,'sample','cards',batch(rows))
        proof={'tag':TAGS[0],'batch':'001','sort':'最新','reason':'超过时间窗口','capturedAt':stamp()}
        self.assertTrue(observe(s,'sample','progress',proof)['complete'])
        s['config']['scan']['days']=7;s['runs']['sample']['scope']['scan']['days']=7
        with self.assertRaises(ValueError):observe(s,'sample','progress',proof)

    def test_keywords_scoped_and_reversible(self):
        s=configured();acquire(s)
        s['keywords']=[{'id':'x','tag':TAGS[0],'word':'设计','field':'title'}]
        self.assertEqual(results(s,'sample')['results'],[])
        observe(s,'sample','cards',batch([card()],TAGS[1]))
        self.assertEqual(final_decision(s)['visibleTags'],[TAGS[1]])
        s['keywords']=[];self.assertEqual(len(results(s,'sample')['results']),1)
        self.assertEqual(s['rejected'],[])

    def test_find_three_independent_conditions(self):
        s=configured();acquire(s)
        before=copy.deepcopy(s)
        result=find(s,[{'company':'示例甲','job':'设计'},{'company':'无此公司','job':'设计'},{'job':'设计'}])
        self.assertEqual([q['matchCount'] for q in result['conditions']],[1,0,1])
        self.assertEqual(result['matches'][0]['matchedConditions'],[1,3])
        self.assertEqual(s,before)
        with self.assertRaises(ValueError):find(s,[{'job':'设计'}]*4)

    def test_find_uses_current_rules_and_applied(self):
        s=configured();acquire(s)
        s['config']['experience']['capital']=EXPERIENCE['capital'].copy()
        self.assertFalse(find(s,[{'job':'设计'}])['matches'])
        s['config']['experience']={};self.assertTrue(find(s,[{'job':'设计'}])['matches'])
        s['applied'].append({'company':'示例甲公司','job':'设计师'})
        self.assertFalse(find(s,[{'job':'设计'}])['matches'])


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        main(['--project',str(self.root),'init'])

    def tearDown(self):self.temp.cleanup()

    def call(self,*args):return main(['--project',str(self.root),*args])

    def test_explicit_project_no_overwrite(self):
        with self.assertRaises(ValueError):self.call('init')
        with self.assertRaises(ValueError):Store(SCRIPTS.parent)

    def test_unconfirmed_does_not_write(self):
        path=self.root/'zhilian-state.json';old=path.read_bytes()
        with self.assertRaises(ValueError):self.call('experience','set','capital')
        self.assertEqual(old,path.read_bytes())

    def test_optional_experience_full_lifecycle(self):
        initial=(self.root/'zhilian-state.json').read_bytes()
        self.call('experience','preview','all')
        self.assertEqual(initial,(self.root/'zhilian-state.json').read_bytes())
        self.call('experience','set','capital','--confirmed')
        self.assertIn('capital',self.call('experience','show'))
        opts=self.root/'options.json';opts.write_text(json.dumps({'threshold':1000000,'action':'remind'}))
        self.call('experience','set','capital','--options',str(opts),'--confirmed')
        self.assertEqual(self.call('experience','show')['capital']['threshold'],1000000)
        self.call('experience','clear','--confirmed');self.assertEqual(self.call('experience','show'),{})
        self.assertTrue(list((self.root/'backups').glob('*.json')))

    def test_lock_prevents_other_actor(self):
        self.call('--actor','worker-a','relay','claim')
        with self.assertRaises(ValueError):self.call('--actor','worker-b','experience','set','span','--confirmed')
        self.call('--actor','worker-a','relay','release')

    def test_reported_violation_lets_relay_release_but_keeps_record(self):
        tags=self.root/'config.json';tags.write_text(json.dumps(default_config()|{'tags':TAGS}))
        self.call('config','apply',str(tags),'--confirmed')
        self.call('--actor','worker-a','relay','claim')
        names=self.root/'applied.json';names.write_text(json.dumps({'source':'https://i.zhaopin.com/','capturedAt':now(),'total':0,'rows':[]}))
        self.call('--actor','worker-a','applied','sync','--input',str(names),'--confirmed')
        self.call('--actor','worker-a','run','start','sample')
        cards=self.root/'cards.json';cards.write_text(json.dumps(batch([card()])))
        k=self.call('--actor','worker-a','local','sample','cards','--input',str(cards))[0]['key']
        click=self.root/'click.json';click.write_text(json.dumps({'tag':TAGS[0],'batch':'001','items':[{'key':k,'heading':'设计师','id':'CC100J1001'}]}))
        self.assertFalse(self.call('--actor','worker-a','local','sample','clickbatch','--input',str(click))['ok'])
        with self.assertRaises(ValueError):self.call('--actor','worker-a','relay','release')
        with self.assertRaises(ValueError):self.call('--actor','worker-a','violations','ack','--run','sample')
        self.call('--actor','worker-a','violations','ack','--run','sample','--message','已告知使用者：点了未放行的卡片')
        with self.assertRaises(ValueError):self.call('--actor','worker-a','violations','ack','--run','sample','--message','重复登记')
        self.call('--actor','worker-a','relay','release')
        kept=self.call('violations','list','--run','sample')
        self.assertEqual(len(kept),1);self.assertIn('已告知使用者',kept[0]['acknowledged']['message'])
        self.assertFalse(self.call('results','sample')['scanComplete'])
        self.call('--actor','worker-b','relay','claim')

    def test_atomic_rollback(self):
        store=Store(self.root);before=store.file.read_bytes()
        with self.assertRaises(ValueError):
            with store.transaction() as state:
                state['config']['tags']=['bad'];raise ValueError('simulate failure')
        self.assertEqual(before,store.file.read_bytes())

    def test_external_concurrent_change_not_overwritten(self):
        store=Store(self.root);external=store.read();external['revision']=50
        with self.assertRaises(ValueError):
            with store.transaction() as state:
                state['config']['tags']=['new']
                store.file.write_text(json.dumps(external))
        self.assertEqual(store.read()['revision'],50)

    def test_real_cli_help_and_exit(self):
        result=subprocess.run([sys.executable,str(SCRIPTS/'zhilian.py'),'--help'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0)
        result=subprocess.run([sys.executable,str(SCRIPTS/'zhilian.py'),'--project',str(self.root),'experience','set','capital'],capture_output=True,text=True)
        self.assertEqual(result.returncode,2)

    def test_cli_end_to_end_config_observations_find_and_revoke(self):
        def submit(command, payload, *rest):
            path=self.root/'input.json';path.write_text(json.dumps(payload,ensure_ascii=False))
            return self.call(*command,'--input',str(path),*rest)
        cfg=default_config();cfg['tags']=TAGS[:]
        path=self.root/'config.json';path.write_text(json.dumps(cfg))
        self.call('config','apply',str(path),'--confirmed')
        submit(['applied','sync'],{'source':'https://i.zhaopin.com/','capturedAt':now(),'total':0,'rows':[]},'--confirmed')
        self.call('run','start','sample')
        k=submit(['local','sample','cards'],batch([card()]))[0]['key']
        submit(['local','sample','select'],{'tag':TAGS[0],'batch':'001','keys':[k],'rows':[card()]})
        submit(['local','sample','clickbatch'],{'tag':TAGS[0],'batch':'001','items':[{'key':k,'heading':'设计师','id':'CC100J1001'}]})
        submit(['local','sample','detail'],detail_payload(card(),k))
        submit(['local','sample','company'],company_payload())
        self.call('local','sample','judge')
        self.assertEqual(len(self.call('find','--job','设计')['matches']),1)
        self.call('experience','set','capital','--confirmed')
        self.assertEqual(self.call('find','--job','设计')['matches'],[])
        self.call('experience','remove','capital','--confirmed')
        self.assertEqual(len(self.call('find','--job','设计')['matches']),1)
        state=Store(self.root).read();evidence=copy.deepcopy(state['runs']['sample']['evidence'])
        self.call('keywords','add','--tag',TAGS[0],'--word','设计','--confirmed')
        self.assertEqual(self.call('find','--job','设计')['matches'],[])
        rid=self.call('keywords','list')[0]['id']
        self.call('keywords','remove','--id',rid,'--confirmed')
        self.assertEqual(len(self.call('find','--job','设计')['matches']),1)
        self.assertEqual(Store(self.root).read()['runs']['sample']['evidence'],evidence)
        before=(self.root/'zhilian-state.json').read_bytes()
        self.call('results','sample');self.call('find','--job','设计');self.call('pending')
        self.assertEqual(before,(self.root/'zhilian-state.json').read_bytes())

    def test_invalid_batch_does_not_partial_write(self):
        path=self.root/'input.json';path.write_text(json.dumps({'source':'https://i.zhaopin.com/','capturedAt':now(),'total':2,'rows':[{'company':'X','job':'Y'},{'company':'Z','job':''}]}))
        before=(self.root/'zhilian-state.json').read_bytes()
        with self.assertRaises(ValueError):self.call('applied','sync','--input',str(path),'--confirmed')
        self.assertEqual(before,(self.root/'zhilian-state.json').read_bytes())


if __name__ == '__main__': unittest.main()
