"""Public configuration, identity and transactional storage. Standard library only."""
import copy
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import unicodedata
from contextlib import contextmanager


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('时间须为带时区的 ISO 字符串')
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('时间格式错误') from None
    if parsed.tzinfo is None:
        raise ValueError('时间必须带时区')
    return parsed


def digest(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def text(value):
    return unicodedata.normalize('NFKC', str(value or '')).strip().lower()


def company(value):
    s = re.sub(r'[\s()·]', '', text(value))
    return re.sub(r'(有限责任公司|股份有限公司|有限公司)$', '', s)


def job(value):
    s = re.sub(r'\(j\d+\)$', '', text(value))
    return re.sub(r'\s', '', s).replace('人事行政', '行政人事')


def identity(row, config):
    co = company(row.get('company') or row.get('companyName') or row.get('brand'))
    nm = job(row.get('job') or row.get('name'))
    for group in config.get('companyAliases', []):
        if co in [company(x) for x in group]:
            co = company(group[0])
            break
    if not co or not nm:
        raise ValueError('缺公司或岗位名称')
    return co, nm


def same(a, b, config):
    return identity(a, config) == identity(b, config)


def key(row, config):
    return digest(identity(row, config))[:24]


EXPERIENCE = {
    'capital': {'action': 'exclude', 'threshold': 500000},
    'span': {'action': 'exclude', 'threshold': 9},
    'mandatory_overtime': {'action': 'exclude'},
    'residential_address': {'action': 'remind'},
    'hiring_structure': {'action': 'remind', 'sizeBelow': 20, 'rolesAtLeast': 5},
    'sparse_description': {'action': 'remind'},
    'reviews': {'action': 'remind', 'categories': [], 'threshold': 1, 'mode': 'combined'},
    'typos': {'action': 'exclude'},
}
NUMERIC_FIELDS = {'capital_cny', 'work_span_hours', 'net_work_hours', 'start_hour', 'end_hour',
                  'salary_min', 'salary_max', 'experience_years', 'company_size',
                  'company_age_years', 'commute_minutes'}
TEXT_FIELDS = {'title', 'company', 'industry', 'work_address', 'description', 'company_intro',
               'employment_type', 'education', 'rest_schedule', 'company_type', 'district', 'street'}


def default_config():
    return {'version': 1, 'tags': [], 'scan': {'days': None, 'sort': '最新'},
            'webpage': {}, 'rules': [], 'experience': {}, 'companyAliases': [],
            'applicationPolicy': {'reposts': 'remind', 'reuseReviewed': False}}


def number(value, positive=False):
    return type(value) in (int, float) and math.isfinite(value) and (value > 0 if positive else value >= 0)


def strings(values, allow_empty=False):
    return (isinstance(values, list) and (allow_empty or bool(values))
            and all(isinstance(v, str) and v.strip() for v in values)
            and len(set(values)) == len(values))


def validate_config(config, ready=False):
    if not isinstance(config, dict) or set(config) not in (set(default_config()), set(default_config()) - {'applicationPolicy'}) or config['version'] != 1:
        raise ValueError('配置字段或版本不符；用 config show 查看模板')
    policy = config.get('applicationPolicy', {'reposts': 'remind', 'reuseReviewed': False})
    if (not isinstance(policy, dict) or set(policy) != {'reposts', 'reuseReviewed'}
            or policy['reposts'] not in ('remind', 'skip') or type(policy['reuseReviewed']) is not bool):
        raise ValueError('applicationPolicy 须含 reposts:remind/skip 和 reuseReviewed 布尔值')
    tags = config['tags']
    if not strings(tags, True) or any('|' in t or '\n' in t for t in tags):
        raise ValueError('标签必须是不同的非空名称')
    if ready and len(tags) != 3:
        raise ValueError('先从页面读取并确认三个实际期望标签')
    scan = config['scan']
    if not isinstance(scan, dict) or set(scan) != {'days', 'sort'} or scan['sort'] != '最新':
        raise ValueError('当前采集支持最新排序')
    if scan['days'] is not None and (type(scan['days']) is not int or scan['days'] < 1):
        raise ValueError('days 为正整数，或 null 表示翻到没有更多')
    if not isinstance(config['webpage'], dict):
        raise ValueError('webpage 必须是已确认网页条件对象')
    if not isinstance(config['companyAliases'], list):
        raise ValueError('companyAliases 须为明确确认的同主体名称组')
    aliases = []
    for group in config['companyAliases']:
        if not strings(group) or len(group) < 2:
            raise ValueError('每个别名组至少两个名称')
        normalized = [company(x) for x in group]
        if any(v in aliases for v in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError('别名组重叠或名称归一化后重复')
        aliases += normalized
    if not isinstance(config['rules'], list):
        raise ValueError('rules 必须是列表')
    seen = set()
    for rule in config['rules']:
        if not isinstance(rule, dict) or set(rule) != {'id', 'field', 'op', 'value', 'action'}:
            raise ValueError('规则须含 id/field/op/value/action，不能含未支持字段')
        rid, field, op, val = (rule[k] for k in ('id', 'field', 'op', 'value'))
        if not isinstance(rid, str) or not rid.strip() or rid.startswith('exp:') or rid in seen:
            raise ValueError('规则 id 为空、重复或使用经验包保留前缀')
        seen.add(rid)
        if rule['action'] not in ('exclude', 'remind'):
            raise ValueError('动作仅支持 exclude 排除或 remind 提醒')
        valid = ((field in NUMERIC_FIELDS and op in ('gt', 'gte', 'lt', 'lte', 'eq') and number(val))
                 or (field in TEXT_FIELDS and op in ('contains_any', 'not_contains_any', 'in', 'not_in') and strings(val))
                 or (field == 'manual' and op == 'check' and isinstance(val, str) and bool(val.strip())))
        if not valid:
            raise ValueError('未支持或无效的规则：' + rid)
    if not isinstance(config['experience'], dict):
        raise ValueError('experience 必须是对象，未启用项应省略')
    for name, options in config['experience'].items():
        if name not in EXPERIENCE or not isinstance(options, dict) or set(options) != set(EXPERIENCE[name]):
            raise ValueError('未知经验项或参数：' + name)
        if options['action'] not in ('exclude', 'remind'):
            raise ValueError('经验动作须为 exclude/remind')
        if 'threshold' in options and not number(options['threshold'], True):
            raise ValueError('阈值必须为正数')
        if name == 'span' and options['threshold'] > 24:
            raise ValueError('每日跨度不能超过24小时')
        if name == 'hiring_structure' and any(type(options[k]) is not int or options[k] < 1 for k in ('sizeBelow', 'rolesAtLeast')):
            raise ValueError('规模和岗位数阈值须为正整数')
        if name == 'reviews':
            if not strings(options['categories'], True) or options['mode'] not in ('combined', 'separate') or type(options['threshold']) is not int:
                raise ValueError('评价类别、计数方式或条数阈值错误')
            if options['action'] == 'exclude' and not options['categories']:
                raise ValueError('自动排除评价前必须明确负面类别')
    return config


def new_state():
    return {'schema': 1, 'revision': 0, 'config': default_config(), 'keywords': [],
            'applied': [], 'rejected': [], 'appliedSync': None, 'runs': {}, 'relay': {}, 'events': [], 'history': {'seen': [], 'reviewed': [], 'imports': []}}


class Store:
    def __init__(self, project):
        self.root = Path(project).expanduser().resolve()
        package = Path(__file__).resolve().parent.parent
        if self.root == package or package in self.root.parents:
            raise ValueError('用户数据目录必须在公开技能包之外')
        self.file = self.root / 'zhilian-state.json'

    def read(self):
        if not self.file.exists():
            raise ValueError('项目尚未初始化，请先 init')
        state = json.loads(self.file.read_text(encoding='utf-8'))
        if state.get('schema') != 1:
            raise ValueError('数据版本不支持')
        return state

    def init(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / '.zhilian.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if self.file.exists():
                raise ValueError('项目已经初始化，未覆盖')
            self._write(new_state())
        return {'project': str(self.root), 'config': default_config()}

    def _write(self, state):
        fd, temp = tempfile.mkstemp(prefix='.state-', dir=self.root)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                json.dump(state, out, ensure_ascii=False, indent=2, allow_nan=False)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, self.file)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    @contextmanager
    def transaction(self, actor=None):
        if not self.file.exists():
            raise ValueError('项目尚未初始化')
        with (self.root / '.zhilian.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            original = self.file.read_bytes()
            state = self.read()
            holder = state['relay'].get('owner')
            if holder and actor != holder:
                raise ValueError('项目由 ' + holder + ' 占用；先核实接力，不自动抢占')
            before = copy.deepcopy(state)
            yield state
            if state == before:
                return
            if self.file.read_bytes() != original:
                raise ValueError('数据被其他程序改动，未覆盖，请重新读取')
            backup = self.root / 'backups'
            backup.mkdir(exist_ok=True)
            name = f"{state['revision']:08d}-{digest(before)[:12]}.json"
            path = backup / name
            if not path.exists():
                path.write_bytes(original)
                path.chmod(0o600)
            state['revision'] += 1
            state['updatedAt'] = now()
            self._write(state)


def event(state, action, detail):
    state['events'].append({'at': now(), 'action': action, 'detail': copy.deepcopy(detail)})


def effective_revision(state):
    return digest({'config': state['config'], 'keywords': state['keywords'],
                   'applied': state['applied'], 'rejected': state['rejected'],
                   'reviewedHistory': state.get('history', {}).get('reviewed', [])})
