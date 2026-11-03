#!/usr/bin/env python3
"""Portable local CLI. Browser observations arrive as JSON; no network operations."""
import argparse
import copy
import json
from pathlib import Path
import sys
from core import (Store, EXPERIENCE, default_config, validate_config, timestamp, now,
                  identity, same, digest, event, effective_revision, text)
from rules import evaluate, keyword_matches
from workflow import begin, observe, results, pending, find, webpage, run_of
from repeats import annotate_history, applied_time, check_applied_date, supersede, newer_application


from history import import_history


def read_json(path):
    return json.load(sys.stdin) if path == '-' else json.loads(Path(path).read_text(encoding='utf-8'))


def confirmed(args):
    if not args.confirmed:
        raise ValueError('需要使用者已明确授权该选择；授权后调用时带 --confirmed，不由沉默推断')


def parser():
    p = argparse.ArgumentParser(description='智联公开版：本地配置、筛选、已投去重、定位和续查；不操作招聘网站')
    p.add_argument('--project', required=True, help='使用者独立数据目录，必须位于技能包之外')
    p.add_argument('--actor', help='已占用接力时使用同一个执行者名称')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('init')
    sub.add_parser('status')
    sub.add_parser('pending')
    h = sub.add_parser('history')
    h.add_argument('operation', choices=['import', 'summary'])
    h.add_argument('--input', default='-')
    h.add_argument('--confirmed', action='store_true')
    c = sub.add_parser('config')
    c.add_argument('operation', choices=['show', 'validate', 'apply'])
    c.add_argument('file', nargs='?')
    c.add_argument('--confirmed', action='store_true')
    e = sub.add_parser('experience')
    e.add_argument('operation', choices=['catalog', 'show', 'preview', 'set', 'remove', 'clear'])
    e.add_argument('name', nargs='?', choices=list(EXPERIENCE) + ['all'])
    e.add_argument('--options', help='覆盖选中项目的参数 JSON 文件')
    e.add_argument('--confirmed', action='store_true')
    r = sub.add_parser('run')
    r.add_argument('operation', choices=['start'])
    r.add_argument('name')
    local = sub.add_parser('local', aliases=['zlocal'])
    local.add_argument('run')
    local.add_argument('action', choices=['cards', 'empty', 'select', 'clickbatch', 'identify', 'detail', 'company', 'review', 'progress', 'judge'])
    local.add_argument('--input', default='-')
    result = sub.add_parser('results')
    result.add_argument('run')
    a = sub.add_parser('applied')
    a.add_argument('operation', choices=['sync', 'add', 'annotate', 'list'])
    a.add_argument('--input', default='-')
    a.add_argument('--confirmed', action='store_true')
    repeat = sub.add_parser('repeat')
    repeat.add_argument('operation', choices=['list'])
    repeat.add_argument('--run', required=True)
    reject = sub.add_parser('rejected')
    reject.add_argument('operation', choices=['add', 'remove', 'list'])
    reject.add_argument('--input', default='-')
    reject.add_argument('--confirmed', action='store_true')
    f = sub.add_parser('find', aliases=['zfind'])
    f.add_argument('--company')
    f.add_argument('--job')
    f.add_argument('--batch', help='含 queries 数组的 JSON 文件，最多3项')
    f.add_argument('--run')
    k = sub.add_parser('keywords', aliases=['zblock'])
    k.add_argument('operation', choices=['list', 'summary', 'preview', 'add', 'remove'])
    k.add_argument('--tag')
    k.add_argument('--word')
    k.add_argument('--id')
    k.add_argument('--run')
    k.add_argument('--confirmed', action='store_true')
    relay = sub.add_parser('relay')
    relay.add_argument('operation', choices=['claim', 'step', 'release'])
    relay.add_argument('--message', default='')
    v = sub.add_parser('violations')
    v.add_argument('operation', choices=['list', 'ack'])
    v.add_argument('--run', required=True)
    v.add_argument('--message', default='', help='已向使用者如实报告的内容')
    return p


def sync_applied(state, payload):
    if not isinstance(payload, dict) or type(payload.get('total')) is not int or not isinstance(payload.get('rows'), list) or payload['total'] != len(payload['rows']):
        raise ValueError('名单分页总数不符，整批未导入')
    webpage(payload.get('source'))
    timestamp(payload.get('capturedAt'))
    for row in payload['rows']:
        if not isinstance(row, dict) or not {'company', 'job'} <= set(row) <= {'company', 'job', 'appliedAt'} or any(not isinstance(row.get(k), str) or not row[k].strip() for k in ('company', 'job')):
            raise ValueError('名单每行只能有非空 company/job 和页面显示的投递日期 appliedAt；不得导入聊天或反馈正文')
        if row.get('appliedAt') is not None:
            check_applied_date(row['appliedAt'], payload['capturedAt'])
    added = dated = 0
    for row in payload['rows']:
        matches = [old for old in state['applied'] if same(row, old, state['config'])]
        if not matches:
            state['applied'].append(dict(row, source=payload['source'], importedAt=payload['capturedAt'],
                                         appliedAt=row.get('appliedAt'), origin='history_sync'))
            added += 1
            continue
        # The page date fills a missing date or records a later application; it never rewinds a known one.
        for old in matches:
            if row.get('appliedAt') and newer_application(old, row['appliedAt']):
                old.update(appliedAt=row['appliedAt'], dateSource='history_page')
                dated += 1
    state['appliedSync'] = {'id': digest([now(), payload]), 'source': payload['source'],
                            'capturedAt': payload['capturedAt'], 'total': payload['total'], 'at': now()}
    event(state, 'applied_sync', state['appliedSync'])
    return {'read': len(payload['rows']), 'added': added, 'datesFilled': dated, 'totalStored': len(state['applied']), 'sync': state['appliedSync']}


def keyword_inventory(state, run_id):
    if run_id is None:
        if not state['runs']:
            raise ValueError('尚无采集清单')
        run_id = max(state['runs'], key=lambda k: state['runs'][k].get('updatedAt', state['runs'][k]['createdAt']))
    run = run_of(state, run_id)
    seen, rows = set(), []
    for value in run['cards'].values():
        card, tag = value['card'], value['tag']
        ident = (tag, *identity(card, state['config']))
        if ident in seen:
            continue
        seen.add(ident)
        rows.append({'tag': tag, 'company': card['companyName'], 'job': card['name']})
    return {'source': run_id, 'sourceDate': run['createdAt'], 'scanComplete': results(state, run_id)['scanComplete'], 'rows': rows}


def proposed_keyword(args, state):
    if args.tag not in state['config']['tags'] or not isinstance(args.word, str) or not args.word.strip():
        raise ValueError('需要已确认的实际标签和非空标题词')
    return {'id': digest([args.tag, text(args.word)])[:16], 'tag': args.tag, 'word': args.word.strip(), 'field': 'title'}


def main(argv=None):
    args = parser().parse_args(argv)
    store = Store(args.project)
    if args.command == 'init':
        return store.init()
    # Read-only commands have no side effects (including no initialization or refresh writes).
    state = store.read()
    command = {'zlocal': 'local', 'zfind': 'find', 'zblock': 'keywords'}.get(args.command, args.command)
    op = getattr(args, 'operation', None)
    if command == 'status':
        return {'revision': state['revision'], 'relay': state['relay'], 'config': state['config'],
                'appliedCount': len(state['applied']), 'rejectedCount': len(state['rejected']),
                'runs': [{'name': k, 'createdAt': v['createdAt'], 'progress': v['progress']} for k, v in state['runs'].items()]}
    if command == 'history' and op == 'summary':
        h = state.get('history', {})
        return {k: len(h.get(k, [])) for k in ('seen', 'reviewed', 'imports')} | {'applied': len(state['applied']), 'rejected': len(state['rejected']), 'freshSync': False}
    if command == 'pending':
        return pending(state)
    if command == 'results':
        return results(state, args.run)
    if command == 'find':
        if args.batch and (args.company or args.job):
            raise ValueError('批量与单项查询不能同时给出')
        queries = read_json(args.batch)['queries'] if args.batch else [dict(job=args.job, **({'company': args.company} if args.company else {}))]
        return find(state, queries, args.run)
    if command == 'config' and op in ('show', 'validate'):
        return state['config'] if op == 'show' else validate_config(read_json(args.file))
    if command == 'experience' and op in ('catalog', 'show', 'preview'):
        if op == 'catalog':
            return {'suggestions': EXPERIENCE, 'enabled': state['config']['experience'], 'note': '仅展示；默认不启用。'}
        if op == 'show':
            return state['config']['experience']
        if not args.name:
            raise ValueError('需要经验项名称')
        proposed = copy.deepcopy(state['config'])
        selections = EXPERIENCE if args.name == 'all' else {args.name: EXPERIENCE[args.name]}
        if args.options and args.name == 'all':
            raise ValueError('逐项修改参数；整包预览不接受混用参数')
        for name, template in selections.items():
            proposed['experience'][name] = template | (read_json(args.options) if args.options else {})
        validate_config(proposed)
        shadow = copy.deepcopy(state)
        shadow['config'] = proposed
        return {'proposed': proposed['experience'], 'saved': False,
                'impact': [{'run': rid, 'before': results(state, rid), 'after': results(shadow, rid)} for rid in state['runs']]}
    if command in ('applied', 'rejected') and op == 'list':
        return state[command]
    if command == 'repeat' and op == 'list':
        return {'run': args.run, 'reminders': results(state, args.run)['repostReminders']}
    if command == 'keywords' and op == 'list':
        return state['keywords']
    if command == 'violations' and op == 'list':
        return run_of(state, args.run)['violations']
    if command == 'keywords' and op in ('summary', 'preview'):
        inventory = keyword_inventory(state, args.run)
        if op == 'summary':
            groups = {}
            for row in inventory['rows']:
                group = groups.setdefault((row['tag'], row['job']), {'tag': row['tag'], 'title': row['job'], 'companies': []})
                group['companies'].append(row['company'])
            return {k: v for k, v in inventory.items() if k != 'rows'} | {'titleGroups': [g | {'count': len(g['companies'])} for g in groups.values()]}
        rule = proposed_keyword(args, state)
        matches = [r for r in inventory['rows'] if r['tag'] == rule['tag'] and text(rule['word']) in text(r['job'])]
        return {k: v for k, v in inventory.items() if k != 'rows'} | {'proposed': rule, 'matchCount': len(matches), 'matches': matches, 'saved': False,
                'warning': '按标题字面包含，否定表述也会命中；不影响公司名、JD或其他标签。'}
    with store.transaction(args.actor) as state:
        if command == 'history':
            confirmed(args)
            return import_history(state, read_json(args.input))
        if command == 'config':
            confirmed(args)
            cfg = validate_config(read_json(args.file))
            state['config'] = cfg
            event(state, 'config_apply', cfg)
            return {'config': cfg, 'recompute': 'results/find 自动使用最新规则；扫描范围变更需新建采集'}
        if command == 'experience':
            confirmed(args)
            cfg = copy.deepcopy(state['config'])
            if op == 'clear':
                cfg['experience'] = {}
            elif op == 'remove':
                if not args.name or args.name not in cfg['experience']:
                    raise ValueError('该经验项尚未启用')
                del cfg['experience'][args.name]
            elif op == 'set':
                if not args.name:
                    raise ValueError('需要经验项名称')
                if args.name == 'all' and args.options:
                    raise ValueError('修改参数请逐项设置')
                for name in (EXPERIENCE if args.name == 'all' else [args.name]):
                    cfg['experience'][name] = copy.deepcopy(EXPERIENCE[name]) | (read_json(args.options) if args.options else {})
            else:
                raise ValueError('未知经验操作')
            validate_config(cfg)
            state['config'] = cfg
            event(state, 'experience_' + op, cfg['experience'])
            return {'enabled': cfg['experience'], 'configRevision': effective_revision(state)}
        if command == 'run':
            return begin(state, args.name)
        if command == 'local':
            data = {} if args.action == 'judge' and args.input == '-' else read_json(args.input)
            return observe(state, args.run, args.action, data)
        if command in ('applied', 'rejected'):
            confirmed(args)
            payload = read_json(args.input)
            if command == 'applied' and op == 'sync':
                return sync_applied(state, payload)
            if command == 'applied' and op == 'annotate':
                return annotate_history(state, payload)
            if not isinstance(payload, dict) or set(payload) - {'company', 'job', 'appliedAt', 'reason'}:
                raise ValueError('只提交公司、岗位及可选原因/真实投递时间')
            identity(payload, state['config'])
            collection = state[command]
            if op == 'remove':
                removed = [r for r in collection if same(r, payload, state['config'])]
                if not removed:
                    raise ValueError('未找到对应拒绝记录')
                state[command] = [r for r in collection if not same(r, payload, state['config'])]
            elif op == 'add':
                if payload.get('appliedAt'):
                    check_applied_date(payload['appliedAt'])
                matches = [r for r in collection if same(r, payload, state['config'])]
                if command == 'applied' and matches:
                    for old in matches:
                        if payload.get('appliedAt') and old.get('appliedAt') and applied_time(payload['appliedAt']) < applied_time(old['appliedAt']):
                            raise ValueError('不能用较早投递覆盖最近一次投递；历史由事件和备份保留')
                    for old in matches:
                        supersede(old, payload.get('appliedAt'))
                elif not matches:
                    collection.append(payload | {'recordedAt': now(), 'origin': 'user_confirmed'})
            event(state, command + '_' + op, payload)
            return {'count': len(state[command]), 'scope': '仅同公司同岗位'}
        if command == 'keywords':
            confirmed(args)
            if op == 'add':
                rule = proposed_keyword(args, state)
                if not any(r['id'] == rule['id'] for r in state['keywords']):
                    state['keywords'].append(rule)
                event(state, 'keyword_add', rule)
            elif op == 'remove':
                matches = [r for r in state['keywords'] if r['id'] == args.id]
                if not matches:
                    raise ValueError('找不到屏蔽词编号')
                state['keywords'] = [r for r in state['keywords'] if r['id'] != args.id]
                event(state, 'keyword_remove', matches)
            else:
                raise ValueError('未知屏蔽词操作')
            return state['keywords']
        if command == 'relay':
            if not args.actor:
                raise ValueError('接力须提供 --actor')
            relay = state['relay']
            if op == 'claim':
                relay.update(owner=args.actor, since=now(), task=args.message)
            elif op == 'step':
                if relay.get('owner') != args.actor:
                    raise ValueError('请先 claim')
                relay.update(lastAt=now(), message=args.message)
            elif op == 'release':
                if relay.get('owner') != args.actor:
                    raise ValueError('请先 claim')
                if any(not v.get('acknowledged') for r in state['runs'].values() for v in r['violations']):
                    raise ValueError('存在未处理的违规点击：先如实告诉使用者，再用 violations ack 登记报告内容；记录不会删除')
                relay.update(owner=None, releasedAt=now(), next=args.message)
            event(state, 'relay_' + op, relay)
            return relay
        if command == 'violations':
            if not args.message.strip():
                raise ValueError('须用 --message 写明已向使用者如实报告的内容')
            open_items = [v for v in run_of(state, args.run)['violations'] if not v.get('acknowledged')]
            if not open_items:
                raise ValueError('这次采集没有待处理的违规记录')
            for item in open_items:
                item['acknowledged'] = {'at': now(), 'by': args.actor, 'message': args.message.strip()}
            event(state, 'violations_ack', {'run': args.run, 'count': len(open_items), 'message': args.message.strip()})
            return {'run': args.run, 'acknowledged': len(open_items),
                    'note': '记录保留；这次采集仍不算扫描完成，需要时新建采集重扫'}
        raise ValueError('未支持的操作')


if __name__ == '__main__':
    try:
        output = main()
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
        if isinstance(output, dict) and output.get('ok') is False:
            sys.exit(2)
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(json.dumps({'error': str(error), 'changed': '失败命令不提交事务；clickbatch 返回 ok=false 时保留实际点击/违规证据。'}, ensure_ascii=False), file=sys.stderr)
        sys.exit(2)
