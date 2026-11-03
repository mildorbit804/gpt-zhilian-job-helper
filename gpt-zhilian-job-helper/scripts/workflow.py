"""Evidence-preserving local workflow. This module never operates a browser."""
import copy
import datetime as dt
import re
from urllib.parse import urlparse
from core import (now, timestamp, digest, identity, same, key, text, validate_config,
                  effective_revision, event, NUMERIC_FIELDS, TEXT_FIELDS, number)
from list_gate import check_list_evidence
from repeats import repost_status
from history import reviewed_match
from rules import (card_fields, normalize_detail, normalize_company, keyword_matches, evaluate)


def webpage(url):
    p = urlparse(url or '')
    if p.scheme != 'https' or not (p.hostname == 'zhaopin.com' or (p.hostname or '').endswith('.zhaopin.com')) or p.username or p.password:
        raise ValueError('来源须为已观察到的智联 HTTPS 页面链接')
    return url


def company_url_number(url, jobs=False):
    """Read the company from an observed page path, never a job-ID prefix/query."""
    webpage(url)
    pattern = r'/companydetail/jobs-(CZL?\d+)/?' if jobs else r'/companydetail/(CZL?\d+)\.htm'
    match = re.fullmatch(pattern, urlparse(url).path)
    if not match:
        raise ValueError('缺实际公司主页/在招页链接，不能从岗位编号或URL查询参数推断公司')
    return match[1]


def company_reference(card, data):
    evidence, numbers = [], set()
    declared = data['state']['jobDetail']['detailedCompany'].get('companyNumber')
    if declared is not None and declared != '':
        if not isinstance(declared, str) or not re.fullmatch(r'CZL?\d+', declared):
            raise ValueError('详情中的公司编号格式不符，须重新核实页面')
        numbers.add(declared)
        evidence.append({'field': 'detail.companyNumber', 'value': declared, 'source': data['url']})
    for field, url in [('card.companyUrl', card.get('companyUrl')), ('detail.companyUrl', data.get('companyUrl'))]:
        if url is not None and url != '':
            if not isinstance(url, str):
                raise ValueError('实际公司链接须为字符串')
            numbers.add(company_url_number(url))
            evidence.append({'field': field, 'value': url})
    if len(numbers) > 1:
        raise ValueError('岗位详情公司编号与已观察公司链接冲突，须重新核实，不能任选一个')
    return next(iter(numbers), None), evidence


def begin(state, run_id):
    validate_config(state['config'], ready=True)
    if not re.fullmatch(r'[\w-]{1,80}', run_id) or run_id in state['runs']:
        raise ValueError('采集名称无效或已存在；原目录不覆盖')
    sync = state.get('appliedSync')
    if not sync or any(r.get('appliedSync') == sync['id'] for r in state['runs'].values()):
        raise ValueError('本轮正式初筛前须先完整同步投递名单')
    age = (timestamp(now()) - timestamp(sync['capturedAt'])).total_seconds()
    if age < -300 or age > 86400:
        raise ValueError('投递名单采集时间不属于本轮近期同步，请重新读取名单')
    state['runs'][run_id] = {'createdAt': now(), 'appliedSync': sync['id'],
                            'scope': copy.deepcopy({k: state['config'][k] for k in ('tags', 'scan', 'webpage')}),
                            'batches': {}, 'lists': {}, 'cards': {}, 'details': {}, 'companies': {},
                            'manual': {}, 'progress': {}, 'violations': [], 'evidence': []}
    event(state, 'begin', {'run': run_id})
    return {'run': run_id, 'scope': state['runs'][run_id]['scope']}


def run_of(state, run_id):
    if run_id not in state['runs']:
        raise ValueError('没有这次采集，请先 run start')
    return state['runs'][run_id]


def excluded_record(state, row, published=None):
    if any(same(row, r, state['config']) for r in state['rejected']):
        return '用户已拒绝该岗位'
    repost = repost_status(state, row, published)
    if repost and repost['status'] != 'repost':
        return '已投同公司同岗位'
    if reviewed_match(state, row):
        return '历史已核同公司同岗位'
    return None


def published(run, row, config):
    """Latest list publish time seen for this role in the run; detail pages are not trusted for this."""
    times = [v['card'].get('publishTime') for v in run['cards'].values() if same(v['card'], row, config)]
    return max((t for t in times if t), key=timestamp, default=None)


def card_judgment(state, card, tag):
    identity(card, state['config'])
    record = {'company': card['companyName'], 'job': card['name'], 'fields': card_fields(card)}
    why = excluded_record(state, record, card.get('publishTime'))
    words = keyword_matches(state, tag, card['name'])
    result = evaluate(record, state, stage='card')
    reason = why or ('标题屏蔽词' if words else '') or ('已启用规则' if result['excluded'] else '')
    return {'allow': not bool(reason), 'reason': reason, 'evaluation': result, 'keywordRules': words,
            'repost': repost_status(state, record, card.get('publishTime'))}


def token(tag, batch, k):
    return digest([tag, str(batch), k])[:32]


def checked_before(state, card):
    revision = effective_revision(state)
    for run in state['runs'].values():
        if run.get('judgedRevision') != revision:
            continue
        for record in run['details'].values():
            if same(card, record, state['config']):
                co = run['companies'].get(record['companyNumber'])
                result = evaluate(record, state, co, run['manual'].get(record['id']))
                if result['status'] == 'excluded' or (co and not result['pending']):
                    return {'id': record['id'], 'source': record['url'], 'capturedAt': record['capturedAt']}
    return None


def require_context(run, data):
    if data.get('tag') not in run['scope']['tags'] or not isinstance(data.get('batch'), str) or not data['batch']:
        raise ValueError('须提交本轮实际标签及非空字符串 batch')


def observe(state, run_id, action, data):
    result = _observe(state, run_id, action, data)
    run_of(state, run_id)['updatedAt'] = now()
    return result


def _observe(state, run_id, action, data):
    run = run_of(state, run_id)
    if not isinstance(data, dict):
        raise ValueError('输入必须为 JSON 对象')
    if '验证' in data.get('title', ''):
        raise ValueError('VERIFY：停止，让用户处理验证')
    if action in ('cards', 'empty', 'select', 'clickbatch', 'identify', 'detail', 'progress'):
        require_context(run, data)
    scope = {k: state['config'][k] for k in ('tags', 'scan', 'webpage')}
    if action in ('cards', 'empty', 'select', 'progress') and scope != run['scope']:
        raise ValueError('标签、时间窗口或网页条件已变更；请保留本轮证据，新建采集')
    tag, batch = data.get('tag'), data.get('batch')
    if action == 'empty':
        if tag in run['lists'] or any(v['tag'] == tag for v in run['batches'].values()):
            raise ValueError('已有岗位列表，不能用空列表证据覆盖')
        observations = [data.get('after1', {}), data.get('after2', {})]
        for snapshot in observations:
            if snapshot.get('count') != 0 or snapshot.get('cards') != [] or snapshot.get('selectedTag') != tag or snapshot.get('selectedSort') != '最新':
                raise ValueError('空列表须有当前标签、最新排序和零张卡片的证据')
            if not re.search(r'暂无相关职位|没有找到.*职位|暂无职位|没有更多', snapshot.get('endText', '')):
                raise ValueError('缺明确空列表文案')
        if (timestamp(observations[1].get('at')) - timestamp(observations[0].get('at'))).total_seconds() < 2:
            raise ValueError('空列表也须两次稳定读间隔至少2秒')
        run['lists'][tag] = []
        run['batches'][digest([tag, batch])] = {'tag': tag, 'batch': batch, 'count': 0}
        run['progress'][tag] = {'complete': True, 'reason': '空列表', 'batch': batch, 'count': 0, 'at': now()}
        run['evidence'].append({'action': 'empty', 'tag': tag, 'batch': batch,
            'observations': [{k: v.get(k) for k in ('count', 'cards', 'selectedTag', 'selectedSort', 'endText', 'at')} for v in observations]})
        return run['progress'][tag]
    if action == 'cards':
        batch_id = digest([tag, batch])
        if batch_id in run['batches']:
            raise ValueError('批次已存在，不覆盖或重复追加')
        if data.get('sort') != '最新' or not data.get('rows'):
            raise ValueError('需要非空列表和最新排序证据')
        previous = run['lists'].get(tag)
        safe_data = copy.deepcopy(data)
        for phase in ('before', 'after1', 'after2'):
            evidence = safe_data.get('listEvidence', {}).get(phase, {})
            for name in ('cards', 'first3'):
                if isinstance(evidence.get(name), list):
                    evidence[name] = [{k: r[k] for k in ('companyName', 'name')} for r in evidence[name]]
            safe_data['listEvidence'][phase] = {k: v for k, v in evidence.items() if k in ('at', 'count', 'first3', 'cards', 'selectedTag', 'selectedSort')}
        manifest = check_list_evidence(safe_data, previous)
        output_rows = []
        have = {identity(v['card'], state['config']) for v in run['cards'].values()}
        normalized_cards = []
        for index, raw in enumerate(data['rows']):
            # Whitelist job-list facts. Do not persist arbitrary page/account payloads.
            card = {k: copy.deepcopy(v) for k, v in raw.items() if k in (
                'name', 'companyName', 'cityDistrict', 'streetName', 'industryName', 'salary60', 'publishTime', 'companyUrl')}
            k, ident = key(card, state['config']), identity(card, state['config'])
            normalized_cards.append(copy.deepcopy(card))
            decision = card_judgment(state, card, tag)
            duplicate = ident in have
            prior = checked_before(state, card) if decision['allow'] else None
            if duplicate:
                decision.update(allow=False, reason='同标签同公司同岗位重复')
            elif prior:
                decision.update(allow=False, reason='同配置下已核查', previous=prior)
            have.add(ident)
            entry = {'tag': tag, 'batch': batch, 'key': k, 'card': card, 'initial': copy.deepcopy(decision)}
            if duplicate:
                entry['duplicate'] = True
            entry_token = token(tag, batch, k)
            if entry_token not in run['cards']:
                run['cards'][entry_token] = entry
            output_rows.append({'index': index, 'key': k, **decision})
        run['lists'][tag] = copy.deepcopy(manifest)
        run['progress'].pop(tag, None)
        run['batches'][batch_id] = {'tag': tag, 'batch': batch, 'count': len(data['rows'])}
        # Stability identities, not unrelated raw DOM, are retained.
        proof = copy.deepcopy(safe_data['listEvidence'])
        run['evidence'].append({'action': action, 'at': now(), 'tag': tag, 'batch': batch,
                                'listEvidence': proof, 'cards': normalized_cards})
        return output_rows
    if action == 'select':
        keys = data.get('keys')
        if not isinstance(keys, list) or not keys or len(set(keys)) != len(keys):
            raise ValueError('keys 须为非空且不重复的列表')
        selections = []
        for k in keys:
            entry = run['cards'].get(token(tag, batch, k))
            if not entry or entry.get('duplicate') or not card_judgment(state, entry['card'], tag)['allow'] or checked_before(state, entry['card']):
                raise ValueError('目标不在当前放行清单')
            if entry.get('clicked'):
                raise ValueError('此卡已点击，不得重复')
            matches = [i for i, r in enumerate(data['rows']) if same(r, entry['card'], state['config'])]
            if not matches:
                raise ValueError('目标零匹配，整批未选择')
            selections.append({'key': k, 'index': matches[0], 'matches': len(matches)})
        for selection in selections:
            run['cards'][token(tag, batch, selection['key'])]['selection'] = selection | {'revision': effective_revision(state)}
        return selections
    if action in ('clickbatch', 'identify'):
        items = data.get('items') if action == 'clickbatch' else [data]
        if not isinstance(items, list) or not items or any(not isinstance(x, dict) or any(not isinstance(x.get(f), str) or not x[f] for f in ('key', 'id', 'heading')) for x in items):
            raise ValueError('每项需要 key/id/heading')
        output_rows, errors = [], []
        ids = [x['id'] for x in items]
        for item in items:
            entry = run['cards'].get(token(tag, batch, item['key']))
            reason = None
            if not entry or not entry.get('selection'):
                reason = '未放行或未先 select 的点击'
            elif action == 'identify' and not entry.get('clicked'):
                reason = '未登记点击不能补编号'
            elif action == 'clickbatch' and entry.get('clicked'):
                reason = '重复点击'
            elif entry['selection']['revision'] != effective_revision(state):
                reason = '选择后配置或名单发生变化，需重新选择'
            if reason:
                issue = {'key': item['key'], 'reason': reason, 'at': now()}
                run['violations'].append(issue)
                errors.append(issue)
                continue
            entry['clicked'] = True
            expected = re.sub(r'\s', '', entry['card']['name']).rstrip('….')
            heading = re.sub(r'\s', '', item['heading'])
            if not re.fullmatch(r'CC[A-Za-z0-9]+J\d+', item['id']) or ids.count(item['id']) > 1 or not expected or not heading.startswith(expected):
                errors.append({'key': item['key'], 'reason': '标题或编号错配，需重读后 identify'})
                continue
            for other in run['cards'].values():
                if other is not entry and other.get('id') == item['id'] and not same(other['card'], entry['card'], state['config']):
                    reason = '编号已绑定到不同岗位，需重读后 identify'
                    break
            if reason:
                errors.append({'key': item['key'], 'reason': reason})
                continue
            entry['id'] = item['id']
            prior = checked_before(state, entry['card'])
            output_rows.append({'key': item['key'], 'id': item['id'], 'openDetail': not bool(prior),
                            'resume': any(item['id'] in r['details'] for r in state['runs'].values()) and not prior})
        run['evidence'].append({'action': action, 'at': now(), 'tag': tag, 'batch': batch,
                                'items': [{k: i[k] for k in ('key', 'id', 'heading')} for i in items], 'errors': errors})
        return {'results': output_rows, 'errors': errors, 'ok': not errors}
    if action == 'detail':
        entry = run['cards'].get(token(tag, batch, data.get('key')))
        if not entry or not entry.get('clicked') or not entry.get('id'):
            raise ValueError('缺放行、点击或身份核对记录')
        source = webpage(data.get('url'))
        if entry['id'] not in source or data['state'].get('jobNumber') != entry['id']:
            raise ValueError('详情来源链接或编号不符')
        record = normalize_detail(entry['card'], data['state'], source, tag, state['config'])
        record['companyNumber'], record['companyNumberEvidence'] = company_reference(entry['card'], data)
        record['capturedAt'] = data['capturedAt']
        timestamp(data['capturedAt'])
        run['evidence'].append({'action': action, 'at': now(), 'record': copy.deepcopy(record)})
        if record['id'] in run['details']:
            run['manual'].pop(record['id'], None)  # Annotations against an older JD cannot silently carry forward.
        run['details'][record['id']] = record
        run.pop('judgedRevision', None)
        return record
    if action == 'company':
        cn = data.get('companyNumber')
        linked = [r for r in run['details'].values() if r['companyNumber'] == cn and r.get('companyNumberEvidence')]
        if not cn or not linked:
            raise ValueError('公司缺少已采集岗位的关联证据；旧推算编号不能作依据，请重读岗位详情及实际公司链接')
        captured = timestamp(data.get('capturedAt'))
        previous = state.get('lastCompany')
        if previous and previous['id'] != cn and (captured - timestamp(previous['at'])).total_seconds() < 5:
            raise ValueError('不同公司采集间隔不足5秒')
        if data['state'].get('companyCode') != cn or data['jobs'].get('companyCode') != cn:
            raise ValueError('公司主页与在招页编号不一致')
        url, jobs_url = webpage(data.get('url')), webpage(data.get('jobsUrl'))
        if company_url_number(url) != cn or company_url_number(jobs_url, jobs=True) != cn:
            raise ValueError('公司来源链接与编号不符')
        base = (data['state'].get('companyDetail') or {}).get('companyBase') or {}
        if base.get('companyNumber') and base['companyNumber'] != cn:
            raise ValueError('公司主页正文与页面公司编号冲突')
        if base.get('companyName') and any(not same(r, {'company': base['companyName'], 'job': r['job']}, state['config']) for r in linked):
            raise ValueError('公司主页名称与岗位主体不符；须核实，别名仅使用已确认配置')
        co = normalize_company(data['state'], data['jobs'], url, jobs_url)
        co['capturedAt'] = data['capturedAt']
        run['evidence'].append({'action': action, 'at': now(), 'companyNumber': cn, 'company': copy.deepcopy(co)})
        run['companies'][cn] = co
        state['lastCompany'] = {'id': cn, 'at': data['capturedAt']}
        run.pop('judgedRevision', None)
        return co
    if action == 'review':
        return annotate(state, run, data)
    if action == 'progress':
        if data.get('sort') != '最新':
            raise ValueError('缺最新排序证据')
        if digest([tag, batch]) not in run['batches']:
            raise ValueError('进度必须属于已采集批次')
        latest_batch = next(v['batch'] for v in reversed(list(run['batches'].values())) if v['tag'] == tag)
        if batch != latest_batch:
            raise ValueError('终点证据必须对应当前标签最后一批')
        why = data.get('reason')
        completed = False
        if why == '没有更多':
            if not isinstance(data.get('endText'), str) or '没有更多' not in data['endText']:
                raise ValueError('缺页面没有更多证据')
            completed = True
        elif why == '超过时间窗口':
            days = run['scope']['scan']['days']
            if days is None:
                raise ValueError('本轮约定翻到底，未设置时间窗口')
            rows = [card for e in run['evidence'] if e['action'] == 'cards' and e['tag'] == tag for card in e['cards']][-20:]
            if len(rows) != 20:
                raise ValueError('尾部后台日期证据不足20条')
            end_at = timestamp(data.get('capturedAt'))
            dates = [timestamp(r.get('publishTime')) for r in rows]
            real = [t for t in dates if not 0 <= t.hour < 3]
            if len(real) < 10 or sum((end_at - t).total_seconds() > days * 86400 for t in real) < (len(real) + 1) // 2:
                raise ValueError('未达到时间窗口终点条件')
            completed = True
        run['progress'][tag] = {'complete': completed, 'reason': why or '未翻完', 'batch': batch,
                                'count': len(run['lists'].get(tag, [])), 'at': now()}
        run['evidence'].append({'action': action, 'tag': tag, 'proof': {k: data.get(k) for k in ('reason', 'endText', 'capturedAt', 'sort', 'batch')}, 'at': now()})
        return run['progress'][tag]
    if action == 'judge':
        result = results(state, run_id)
        run['judgedRevision'] = effective_revision(state)
        run['evidence'].append({'action': action, 'at': now(), 'configRevision': effective_revision(state), 'judgment': copy.deepcopy(result)})
        return result
    raise ValueError('未知采集操作')


def annotate(state, run, data):
    record = run['details'].get(data.get('id'))
    if not record:
        raise ValueError('只能补充已采集岗位的核查证据')
    if set(data) - {'id', 'facts', 'checks', 'reviewLabels'}:
        raise ValueError('未知人工核查字段')
    update = copy.deepcopy(run['manual'].get(record['id'], {'facts': {}, 'checks': {}, 'reviewLabels': {}}))
    for group in ('facts', 'checks', 'reviewLabels'):
        if group in data and not isinstance(data[group], dict):
            raise ValueError('人工核查须按字段名/规则编号提交对象')
        for name, value in data.get(group, {}).items():
            if value is None:
                update[group].pop(name, None)
                continue
            if not isinstance(value, dict) or not isinstance(value.get('quote'), str) or not value['quote'].strip():
                raise ValueError('人工核查必须带原文 quote')
            webpage(value.get('source'))
            timestamp(value.get('capturedAt'))
            if group == 'facts':
                if name not in NUMERIC_FIELDS | TEXT_FIELDS or (name in NUMERIC_FIELDS and not number(value.get('value'))) or (name in TEXT_FIELDS and not isinstance(value.get('value'), str)):
                    raise ValueError('不支持的事实字段或类型')
                if name in ('title', 'company', 'description', 'company_intro'):
                    raise ValueError('不可用事实补充改写原岗位身份/JD/简介')
            elif group == 'checks':
                supported = {r['id'] for r in state['config']['rules'] if r['field'] == 'manual'} | {'exp:typos', 'exp:sparse_description'}
                if name not in supported or value.get('verdict') not in ('match', 'clear', 'unknown'):
                    raise ValueError('人工规则编号或结论不符')
                if name == 'exp:typos' and value['verdict'] == 'match':
                    if not value.get('correction') or value['quote'] not in (record['fields'].get('description') or ''):
                        raise ValueError('错别字需要 JD 原文及正确写法；疑似用 unknown')
            else:
                co = run['companies'].get(record['companyNumber'], {})
                available = {item['key']: item for g in co.get('reviews', {}).values() for item in g['items']}
                opts = state['config']['experience'].get('reviews', {})
                cats = value.get('categories')
                if name not in available or value['quote'] not in available[name]['text'] or not isinstance(cats, list) or any(c not in opts.get('categories', []) for c in cats):
                    raise ValueError('评价标注必须对应已读原文和用户选定类别')
            update[group][name] = value
    run['manual'][record['id']] = update
    run['evidence'].append({'action': 'review', 'at': now(), 'id': record['id'], 'annotations': copy.deepcopy(update)})
    run.pop('judgedRevision', None)
    return {'id': record['id'], 'annotations': update}


def results(state, run_id):
    run = run_of(state, run_id)
    cfg = state['config']
    rows, suppressed, seen = [], [], set()
    for index, record in enumerate(run['details'].values(), 1):
        ident = identity(record, cfg)
        if ident in seen:
            continue
        seen.add(ident)
        tags = sorted({v['tag'] for v in run['cards'].values() if same(v['card'], record, cfg)})
        visible = [t for t in tags if not keyword_matches(state, t, record['job'])]
        decision = evaluate(record, state, run['companies'].get(record['companyNumber']), run['manual'].get(record['id']))
        row = {k: record[k] for k in ('id', 'company', 'job', 'url', 'capturedAt', 'salaryText', 'district')}
        row.update(sourceIndex=index, visibleTags=visible, **decision)
        row['companyEvidence'] = copy.deepcopy(run['companies'].get(record['companyNumber']))
        posted = published(run, record, cfg)
        why = excluded_record(state, record, posted)
        repost = repost_status(state, record, posted)
        if repost:
            row['repost'] = repost
        if why or (tags and not visible):
            row['suppressedReason'] = why or '标题屏蔽词'
            suppressed.append(row)
        else:
            rows.append(row)
    unresolved = []
    for item in run['cards'].values():
        judgment = card_judgment(state, item['card'], item['tag'])
        if not judgment['allow']:
            continue
        if item.get('initial', {}).get('previous') and checked_before(state, item['card']):
            continue
        if not any(same(item['card'], r, cfg) for r in run['details'].values()):
            unresolved.append({k: item[k] for k in ('tag', 'batch', 'key', 'card')})
    # Use the same current, deduplicated view as results/find/pending. Excluded
    # or suppressed roles need no company follow-up while that reason holds.
    missing = [r['id'] for r in rows if r['status'] != 'excluded' and not r['companyEvidence']]
    complete = all(run['progress'].get(t, {}).get('complete') for t in run['scope']['tags'])
    scope_changed = run['scope'] != {k: state['config'][k] for k in ('tags', 'scan', 'webpage')}
    reminders = [{k: r[k] for k in ('sourceIndex', 'company', 'job', 'url', 'status')} | {'repost': r['repost']}
                 for r in rows if r.get('repost') and r['status'] != 'excluded']
    return {'source': run_id, 'sourceDate': run['createdAt'], 'scope': run['scope'], 'progress': run['progress'],
            'scopeChanged': scope_changed,
            'configRevision': effective_revision(state), 'scanComplete': complete and not unresolved and not missing and not run['violations'] and not scope_changed,
            'reviewComplete': not any(r['pending'] for r in rows), 'results': rows, 'suppressed': suppressed,
            'unresolvedCards': unresolved, 'missingCompanies': missing, 'repostReminders': reminders, 'violations': run['violations'],
            'note': '本地判断不代替人工复核；未采集到的岗位不在此清单，未自动联系或投递。'}


def pending(state):
    output = []
    for name, run in state['runs'].items():
        view = results(state, name)
        unresolved = [r for r in view['unresolvedCards'] if not checked_before(state, r['card'])]
        missing = [rid for rid in view['missingCompanies'] if not checked_before(state, run['details'][rid])]
        reviews = [r for r in view['results'] if r['pending'] and not checked_before(state, r)]
        if unresolved or missing or reviews:
            output.append({'source': name, 'sourceDate': run['createdAt'], 'unresolvedCards': view['unresolvedCards'],
                           'missingCompanies': missing, 'pendingReviews': reviews})
            output[-1]['unresolvedCards'] = unresolved
    historical = [copy.deepcopy(r) for r in state.get('history', {}).get('reviewed', [])
                  if not r.get('completed') or not r.get('company') or not r.get('job')]
    if historical:
        output.append({'source': 'local-history', 'historyReviewRequired': historical,
                       'note': '历史缺身份或未核完记录；不是新岗位候选，不代表当前网页仍在招。'})
    return output


def find(state, queries, run_id=None):
    if not isinstance(queries, list) or not 1 <= len(queries) <= 3:
        raise ValueError('每批1至3个条件，超过请分批')
    for q in queries:
        if not isinstance(q, dict) or set(q) - {'company', 'job'} or not isinstance(q.get('job'), str) or not q['job'].strip() or ('company' in q and (not isinstance(q['company'], str) or not q['company'].strip())):
            raise ValueError('条件须有非空 job，可选非空 company')
    if not state['runs']:
        return {'source': None, 'conditions': [dict(q, matchCount=0) for q in queries], 'matches': [], 'note': '尚无清单，不搜索网站'}
    run_id = run_id or max(state['runs'], key=lambda x: state['runs'][x].get('updatedAt', state['runs'][x]['createdAt']))
    view = results(state, run_id)
    matches, counts = [], [0] * len(queries)
    for row in view['results']:
        if row['status'] == 'excluded':
            continue
        co, nm = identity(row, state['config'])
        conditions = []
        for i, q in enumerate(queries):
            from core import company, job
            query_co = company(q.get('company', ''))
            names = {co, company(row['company'])}
            for group in state['config']['companyAliases']:
                if co in [company(v) for v in group]:
                    names.update(company(v) for v in group)
            if any(query_co in name for name in names) and job(q['job']) in nm:
                conditions.append(i + 1)
                counts[i] += 1
        if conditions:
            matches.append(row | {'matchedConditions': conditions})
    return {k: view[k] for k in ('source', 'sourceDate', 'scanComplete', 'reviewComplete', 'configRevision')} | {
        'conditions': [dict(q, condition=i + 1, matchCount=counts[i]) for i, q in enumerate(queries)], 'matches': matches}
