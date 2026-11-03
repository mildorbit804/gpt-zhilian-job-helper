"""Import a user's authorized local history without pretending it is a fresh website sync."""
import copy
from core import digest, event, identity, same, now
from repeats import check_applied_date


def reviewed_match(state, row):
    if not state['config'].get('applicationPolicy', {}).get('reuseReviewed', False):
        return None
    for old in state.get('history', {}).get('reviewed', []):
        if old.get('completed') is not True:
            continue
        if (old.get('company') and old.get('job')
                and (row.get('company') or row.get('companyName') or row.get('brand'))
                and (row.get('job') or row.get('name')) and same(old, row, state['config'])):
            return old
        # ID-only history cannot be treated as a company-wide or title-wide block.
        if old.get('id') and old['id'] == (row.get('id') or row.get('number')):
            return old
    return None


def import_history(state, payload):
    required = {'version', 'source', 'seen', 'reviewed', 'applied', 'rejected'}
    if not isinstance(payload, dict) or set(payload) != required or payload['version'] != 1:
        raise ValueError('历史导入格式错误')
    if not isinstance(payload['source'], str) or not payload['source'].strip():
        raise ValueError('须说明历史来源')
    if any(not isinstance(payload[k], list) for k in ('seen', 'reviewed', 'applied', 'rejected')):
        raise ValueError('历史记录须为数组')
    if any(not isinstance(x, str) or not x.strip() for x in payload['seen']):
        raise ValueError('seen 只能包含真实已有的岗位编号')
    for group in ('reviewed', 'applied', 'rejected'):
        for row in payload[group]:
            allowed = {'id', 'company', 'job', 'decision', 'completed'} if group == 'reviewed' else {'company', 'job', 'appliedAt', 'reason'}
            if not isinstance(row, dict) or set(row) - allowed:
                raise ValueError('历史字段不符；不得导入聊天、招呼语或反馈正文')
            if any(not isinstance(v, str) or not v.strip() for k, v in row.items() if k != 'completed'):
                raise ValueError('历史记录只接受非空文本；缺失字段省略')
            named = bool(row.get('company') and row.get('job'))
            if bool(row.get('company')) != bool(row.get('job')):
                raise ValueError('公司与岗位名称必须成对')
            if not named and (group != 'reviewed' or not row.get('id')):
                raise ValueError('已投/拒绝须有公司和岗位；已核可只保留真实编号')
            if named:
                identity(row, state['config'])
            if group == 'reviewed' and type(row.get('completed')) is not bool:
                raise ValueError('已核历史必须明确 completed；未核完仍保留但不用于跳过')
            if group == 'reviewed' and not row.get('decision'):
                raise ValueError('已核须保留历史结论，不能把仅看过冒充核完')
            if row.get('appliedAt'):
                check_applied_date(row['appliedAt'])
    # Validate the entire payload before any mutation, also safe for direct library use.
    history = state.setdefault('history', {'seen': [], 'reviewed': [], 'imports': []})
    fingerprint = digest(payload)
    if any(x['digest'] == fingerprint for x in history['imports']):
        return {'duplicate': True, 'digest': fingerprint, 'added': {k: 0 for k in ('seen', 'reviewed', 'applied', 'rejected')}}
    added = {}
    previous = set(history['seen'])
    history['seen'] = list(dict.fromkeys(history['seen'] + payload['seen']))
    added['seen'] = len(history['seen']) - len(previous)
    for group in ('reviewed', 'applied', 'rejected'):
        destination = history['reviewed'] if group == 'reviewed' else state[group]
        added[group] = 0
        for row in payload[group]:
            def equivalent(old):
                if group == 'reviewed':
                    # Keep each authentic historical ID, even if it shares a name.
                    if row.get('id') or old.get('id'):
                        return bool(row.get('id')) and row.get('id') == old.get('id')
                return bool(row.get('company') and old.get('company')) and same(row, old, state['config'])
            if any(equivalent(old) for old in destination):
                continue
            destination.append(copy.deepcopy(row) | {'origin': 'local_history', 'historySource': payload['source'], 'importedAt': now()})
            added[group] += 1
    entry = {'digest': fingerprint, 'source': payload['source'], 'at': now(), 'added': added}
    history['imports'].append(entry)
    event(state, 'history_import', entry)
    return entry | {'freshSync': False, 'note': '历史导入不算今日投递；正式扫描前仍须完整同步当前投递名单。'}
