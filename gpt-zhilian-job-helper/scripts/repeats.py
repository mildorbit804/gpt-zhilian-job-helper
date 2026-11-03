"""Applied roles published again: remind the user when they applied. Never reads messages."""
import datetime as dt
import re
from core import same, timestamp, now, event

CHINA = dt.timezone(dt.timedelta(hours=8))


def applied_time(value):
    """Latest moment an application date can stand for. A bare date counts as that whole day."""
    if isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        day = dt.date.fromisoformat(value)
        return dt.datetime.combine(day, dt.time(23, 59, 59), CHINA)
    return timestamp(value)


def china_date(value):
    return applied_time(value).astimezone(CHINA).date().isoformat()


def check_applied_date(value, latest=None):
    if china_date(value) > china_date(latest or now()):
        raise ValueError('投递日期不能晚于名单读取时间或今天')
    return value


def history(state, row):
    return [r for r in state['applied'] if same(r, row, state['config'])]


def applied_bound(record):
    """Undated records only prove the application happened before they were imported."""
    for field in ('appliedAt', 'importedAt', 'recordedAt'):
        if record.get(field):
            return applied_time(record[field])
    return None


def supersede(record, applied_at=None):
    """The user applied again: earlier dates move to history and stop bounding the latest application."""
    fields = ('appliedAt', 'importedAt', 'recordedAt', 'dateSource', 'origin', 'source')
    previous = {k: v for k in fields if (v := record.pop(k, None)) is not None}
    if previous:
        record.setdefault('earlier', []).append(previous)
    record.update(appliedAt=applied_at, recordedAt=now(), origin='user_confirmed')


def newer_application(record, value):
    """A page date may fill or advance the latest application, never bring back a superseded one."""
    moment = applied_time(value)
    if record.get('appliedAt') and moment <= applied_time(record['appliedAt']):
        return False
    return all(bound is None or moment > bound for bound in map(applied_bound, record.get('earlier', [])))


def repost_status(state, row, published=None):
    records = history(state, row)
    if not records:
        return None
    if state['config'].get('applicationPolicy', {}).get('reposts') == 'skip':
        return {'status': 'applied', 'policy': 'skip', 'note': '按使用者选择，已投同公司同岗位始终跳过'}
    dates = [r['appliedAt'] for r in records if r.get('appliedAt')]
    info = {'appliedAt': china_date(max(dates, key=applied_time)) if dates else None}
    bounds = [applied_bound(r) for r in records]
    if not published or None in bounds:
        return dict(info, status='applied', note='缺发布时间或投递时间，无法判断是否重新发布，按已投跳过')
    latest = max(bounds)
    if timestamp(published) <= latest:
        return dict(info, status='applied')
    if not info['appliedAt']:
        info['appliedBefore'] = latest.astimezone(CHINA).date().isoformat()
    info['reminder'] = (f"这个岗位你 {info['appliedAt']} 投过" if info['appliedAt'] else
                        f"这个岗位你投过（日期没记录，在 {info['appliedBefore']} 之前）") + '，现在重新发布了，再投不投由你决定。'
    return dict(info, status='repost', publishTime=published)


def annotate_history(state, payload):
    if not isinstance(payload, dict) or set(payload) != {'company', 'job', 'appliedAt'}:
        raise ValueError('须提供 company/job/appliedAt；只补记使用者告知或页面显示的投递日期')
    check_applied_date(payload['appliedAt'])
    records = history(state, payload)
    if not records:
        raise ValueError('没有对应已投记录，不能凭空补造')
    known = [applied_time(r['appliedAt']) for r in records if r.get('appliedAt')]
    known += [applied_time(e['appliedAt']) for r in records for e in r.get('earlier', []) if e.get('appliedAt')]
    if known and applied_time(payload['appliedAt']) < max(known):
        raise ValueError('不能用更早的投递覆盖已知最近一次投递')
    for record in records:
        record.update(appliedAt=payload['appliedAt'], dateSource='user_confirmed')
    event(state, 'applied_annotate', payload)
    return {'updated': len(records), 'scope': '仅同公司同岗位；不读取招聘消息'}
