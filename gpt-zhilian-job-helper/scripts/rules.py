"""One configurable evaluator for preview, cards, final results and lookup."""
import html
import re
from core import EXPERIENCE, text, identity, validate_config


def clean(value):
    return html.unescape(re.sub(r'<[^>]*>', ' ', str(value or ''))).strip()


def capital(value):
    s = str(value or '').replace(',', '')
    if not re.search(r'人民币|CNY|RMB', s, re.I):
        return None
    match = re.fullmatch(r'\s*(?:人民币|CNY|RMB)?\s*(\d+(?:\.\d+)?)\s*(亿|万)?\s*元?\s*(?:人民币|CNY|RMB)?\s*', s, re.I)
    if not match or float(match[1]) <= 0:
        return None
    return float(match[1]) * {'亿': 100000000, '万': 10000, None: 1}[match[2]]


def work_times(value):
    pairs = re.findall(r'(?<!\d)(\d{1,2})[:：](\d{2})\s*[-—–~～至到]\s*(\d{1,2})[:：](\d{2})(?!\d)', value)
    spans = set()
    for ah, am, bh, bm in pairs:
        a, b = int(ah) + int(am) / 60, int(bh) + int(bm) / 60
        if int(ah) > 23 or int(bh) > 23 or int(am) > 59 or int(bm) > 59 or b <= a:
            continue  # Cross-day or malformed schedules need sourced human verification.
        spans.add((a, b))
    if len(spans) != 1:
        return {}
    a, b = spans.pop()
    return {'start_hour': a, 'end_hour': b, 'work_span_hours': round(b - a, 6)}


def card_fields(card):
    return {'title': card['name'], 'company': card['companyName'],
            'industry': card.get('industryName') or None,
            'district': card.get('cityDistrict') or None, 'street': card.get('streetName') or None,
            'work_address': None}  # District is not a precise job address.


def normalize_detail(card, state, source, tag, config):
    p = state.get('jobDetail', {}).get('detailedPosition', {})
    c = state.get('jobDetail', {}).get('detailedCompany', {})
    if not p.get('number') or p['number'] != state.get('jobNumber') or not c:
        raise ValueError('详情编号/公司数据缺失或不一致')
    if identity({'company': c.get('companyName'), 'job': p.get('name') or p.get('positionName')}, config) != identity(card, config):
        raise ValueError('详情与放行卡片身份不符')
    desc, intro = clean(p.get('jobDesc') or p.get('description')), clean(c.get('companyDescription'))
    fields = card_fields(card) | {'description': desc or None, 'company_intro': intro or None,
                                 'work_address': p.get('workAddress') or None,
                                 'industry': c.get('industryNameLevel') or card.get('industryName') or None}
    # Unknown platform schemas are not interpreted as verified education/salary facts.
    return {'id': p['number'], 'company': c['companyName'], 'job': card['name'], 'tag': tag,
            'url': source, 'publishTime': p.get('publishTime'), 'salaryText': card.get('salary60', ''),
            'district': card.get('cityDistrict', ''), 'fields': fields,
            'sources': {k: source for k, v in fields.items() if v is not None}}


def normalize_company(state, jobs, source, jobs_source):
    c = state.get('companyDetail')
    if not isinstance(c, dict) or not isinstance(jobs.get('onlinePositions'), list):
        raise ValueError('缺公司资料或在招列表，不能当空列表')
    reviews = {}
    for field, total_field, label in [('companyComment', 'reviewsSum', 'comments'), ('companyInterview', 'total', 'interviews')]:
        block = c.get(field)
        if not isinstance(block, dict) or type(block.get(total_field)) is not int or block[total_field] < 0 or not isinstance(block.get('items'), list):
            reviews[label] = {'total': None, 'items': [], 'source': source, 'complete': False}
            continue
        entries, seen = [], set()
        for item in block['items']:
            content = clean(item.get('content'))
            if not content:
                continue
            # Identical content is conservatively deduplicated, never inflated into extra negatives.
            canonical = text(content)
            if canonical in seen:
                continue
            seen.add(canonical)
            from core import digest
            entries.append({'key': digest(canonical)[:24], 'text': content, 'source': source})
        reviews[label] = {'total': block[total_field], 'items': entries, 'source': source,
                          'complete': len(entries) == block[total_field]}
    b = (c.get('businessInformationData') or {}).get('businessInformationData') or {}
    base, w = c.get('companyBase') or {}, c.get('workTimeTag') or {}
    work = ' '.join(str(w.get(k) or '') for k in ('workTime', 'breakTimeTag', 'overTimeWorkTag')).strip()
    titles = list(dict.fromkeys(p.get('name') or p.get('positionName') for p in jobs['onlinePositions'] if p.get('name') or p.get('positionName')))
    total = jobs.get('onlinePositionsCount')
    if total and not titles:
        raise ValueError('在招数量非零但未读取岗位标题')
    return {'source': source, 'jobsSource': jobs_source, 'workText': work,
            'capitalText': b.get('registeredCapital') or '', 'registeredAddress': b.get('location') or '',
            'createdDate': b.get('createDate') or '', 'companyType': b.get('epType') or '',
            'sizeText': base.get('companySize') or '', 'titles': titles, 'jobsTotal': total,
            'reviews': reviews}


def keyword_matches(state, tag, name):
    return [r for r in state['keywords'] if r['tag'] == tag and text(r['word']) in text(name)]


def check_manual(manual, rid):
    v = manual.get('checks', {}).get(rid)
    if not v or v.get('verdict') == 'unknown':
        return None, '需人工核实'
    return v['verdict'] == 'match', v['quote'] + ' [' + v['source'] + ']'


def compare(value, rule):
    if value is None or value == '':
        return None
    op, target = rule['op'], rule['value']
    if op in ('gt', 'gte', 'lt', 'lte', 'eq'):
        if type(value) not in (int, float):
            return None
        return {'gt': value > target, 'gte': value >= target, 'lt': value < target,
                'lte': value <= target, 'eq': value == target}[op]
    if op in ('contains_any', 'not_contains_any'):
        match = any(text(t) in text(value) for t in target)
        return match if op == 'contains_any' else not match
    match = text(value) in [text(v) for v in target]
    return match if op == 'in' else not match


def evaluate(record, state, co=None, manual=None, stage='detail'):
    cfg = validate_config(state['config'])
    co, manual = co or {}, manual or {}
    fields = dict(record.get('fields', {}))
    sources = dict(record.get('sources', {}))
    if co:
        jd_rests = set(re.findall(r'单双休|大小周|双休|单休|轮休|排班', fields.get('description') or ''))
        company_rests = set(re.findall(r'单双休|大小周|双休|单休|轮休|排班', co.get('workText') or ''))
        rest_conflict = bool(jd_rests and company_rests and jd_rests != company_rests)
        rest = None if rest_conflict else ' / '.join(sorted(jd_rests | company_rests)) or None
        fields.update({'capital_cny': capital(co.get('capitalText')), 'company_type': co.get('companyType') or None,
                       'rest_schedule': rest})
        sources.update({k: co.get('source') for k in ('capital_cny', 'company_type', 'rest_schedule')})
        combined = '\n'.join([fields.get('description') or '', co.get('workText') or ''])
        for field, value in work_times(combined).items():
            fields[field], sources[field] = value, record.get('url', '') + ' / ' + co.get('source', '')
    for name, fact in manual.get('facts', {}).items():
        fields[name], sources[name] = fact['value'], fact['source']
    excluded, reminders, pending = [], [], []
    def add(rid, matched, action, evidence, value=None):
        item = {'rule': rid, 'action': action, 'evidence': evidence, 'value': value}
        if matched is None:
            if stage != 'card':
                pending.append(item)
        elif matched:
            (excluded if action == 'exclude' else reminders).append(item)
    def add_platform(rid, matched, action, evidence, value=None, gap='平台资料不全，无法判断'):
        # A remind-only item cannot be settled when the platform withholds the data;
        # show the gap instead of holding the role in pending forever.
        if matched is None and action == 'remind':
            if stage != 'card':
                reminders.append({'rule': rid, 'action': 'verify', 'evidence': gap + '（只提醒项，不阻止交付）', 'value': value})
        else:
            add(rid, matched, action, evidence, value)
    for rule in cfg['rules']:
        if rule['field'] == 'manual':
            matched, evidence = check_manual(manual, rule['id'])
            add(rule['id'], matched, rule['action'], evidence)
        else:
            value = fields.get(rule['field'])
            add(rule['id'], compare(value, rule), rule['action'], sources.get(rule['field']), value)
    for name, opts in cfg['experience'].items():
        rid, action = 'exp:' + name, opts['action']
        if name in ('capital', 'span'):
            field, op = ('capital_cny', 'lte') if name == 'capital' else ('work_span_hours', 'gt')
            value = fields.get(field)
            add_platform(rid, compare(value, {'op': op, 'value': opts['threshold']}), action, sources.get(field), value)
        elif name == 'mandatory_overtime':
            value = fields.get('description')
            matches = [] if not value else list(re.finditer(r'(必须|务必|一定要)[^，。；,;\n]{0,12}加班', value))
            positive = any(not re.search(r'(不|并非|不是|无需)\s*$', value[max(0, m.start()-4):m.start()]) for m in matches)
            add_platform(rid, None if not value else positive, action, sources.get('description'), value)
        elif name == 'residential_address':
            value = co.get('registeredAddress')
            add_platform(rid, None if not value else bool(re.search(r'住宅|居民楼|公寓|小区|\d+号楼\d+门', value)), action, co.get('source'), value)
        elif name == 'hiring_structure':
            size = co.get('sizeText', '')
            m = re.fullmatch(r'\s*(\d+)\s*人以下\s*', size)
            above = re.fullmatch(r'\s*(\d+)\s*人以上\s*', size)
            titles = co.get('titles')
            count = len({job for job in titles or [] if re.search(r'行政|文员|助理|出纳|前台|内勤|人事', job)})
            enough = count >= opts['rolesAtLeast']
            complete = titles is not None and co.get('jobsTotal') == len(titles)
            matched = None
            range_size = re.fullmatch(r'\s*(\d+)\s*[-~～至]\s*(\d+)\s*人\s*', size)
            actual_size = fields.get('company_size')
            if actual_size is not None and actual_size >= opts['sizeBelow']:
                matched = False
            elif range_size and int(range_size[1]) >= opts['sizeBelow']:
                matched = False
            elif above and int(above[1]) >= opts['sizeBelow']:
                matched = False
            elif (m and int(m[1]) <= opts['sizeBelow'] or actual_size is not None and actual_size < opts['sizeBelow']) and (enough or complete):
                matched = enough
            add_platform(rid, matched, action, co.get('jobsSource'), {'size': size, 'readRoles': count, 'coverageComplete': complete},
                         '公司规模或在招岗位资料不全，无法判断')
        elif name in ('sparse_description', 'typos'):
            matched, evidence = check_manual(manual, rid)
            add(rid, matched, action, evidence)
        elif name == 'reviews':
            groups = co.get('reviews', {})
            counts, unique, missing_labels = {}, set(), False
            selected, all_rows = [], []
            for group in ('comments', 'interviews'):
                count = 0
                for item in groups.get(group, {}).get('items', []):
                    all_rows.append(item)
                    labels = manual.get('reviewLabels', {}).get(item['key'])
                    if labels is None:
                        missing_labels = True
                        continue
                    if set(labels['categories']) & set(opts['categories']):
                        count += 1
                        if item['key'] not in unique:
                            unique.add(item['key'])
                            selected.append(item)
                counts[group] = count
            full = all(groups.get(g, {}).get('complete') for g in ('comments', 'interviews'))
            value = {'counts': counts, 'uniqueNegativeCount': len(unique), 'coverageComplete': full,
                     'read': all_rows, 'selected': selected}
            if action == 'remind':
                add(rid, bool(all_rows), action, co.get('source'), value)
                if not full:
                    add_platform(rid, None, action, co.get('source'), value, '评价/面试资料未读取完整')
            else:
                threshold_met = (len(unique) >= opts['threshold'] if opts['mode'] == 'combined'
                                 else any(n >= opts['threshold'] for n in counts.values()))
                add(rid, True if threshold_met else (None if not full or missing_labels else False), action, co.get('source'), value)
    if stage != 'card' and not co:
        pending.append({'rule': 'evidence:company', 'action': 'verify', 'evidence': '公司资料尚未采集'})
    if stage != 'card' and co:
        if rest_conflict:
            reminders.append({'rule': 'evidence:rest_conflict', 'action': 'verify', 'evidence': '岗位JD与公司作息标注冲突，见原文来源'})
        # Missing website fields remain explicit even when no optional exclusion is enabled.
        for name, available in [('work_time', bool(co.get('workText'))), ('capital', bool(co.get('capitalText')))]:
            if not available:
                reminders.append({'rule': 'evidence:' + name, 'action': 'verify', 'evidence': '平台未提供，需用户核实'})
    # One evidenced exclusion settles this role under the current rules. Keep all
    # unchecked items visible as unexamined, not as work still required or passed.
    # This is a derived view: removing that exclusion reopens them on evaluation.
    not_required = pending if excluded else []
    if excluded:
        pending = []
    return {'status': 'excluded' if excluded else ('pending' if pending else 'candidate'),
            'excluded': excluded, 'reminders': reminders, 'pending': pending,
            'notRequired': not_required, 'reviewRequired': stage != 'card' and not bool(excluded),
            'fields': fields, 'sources': sources}
