"""Ported list-stability gate; no personal filtering rules."""
from core import timestamp

def check_list_evidence(data,previous=None):
    e=data.get('listEvidence')
    if not isinstance(e,dict):raise ValueError('缺列表稳定证据listEvidence')
    before,a,b=(e.get(k,{}) for k in ('before','after1','after2'))
    def stamp(x):
        t=timestamp(x['at'])
        if t.tzinfo is None:raise ValueError('采集时间必须带时区')
        return t
    t0,t1,t2=map(stamp,(before,a,b))
    if (t2-t1).total_seconds()<2 or (t1-t0).total_seconds()<0:raise ValueError('稳定读间隔需至少2秒且时间顺序正确')
    if e.get('mode')=='switch' and (t2-t0).total_seconds()>15:raise ValueError('切换总等待不超过15秒')
    def identities(x):
        cards=x.get('cards',[])
        if len(cards)!=x.get('count') or not cards:raise ValueError('卡片总数和证据列表不符')
        v=[(p['companyName'],p['name']) for p in cards]
        if x.get('first3')!=cards[:3]:raise ValueError('前3张与完整列表不符')
        return v
    old,one,two=map(identities,(before,a,b))
    if one!=two:raise ValueError('连续两次列表不一致')
    for x in (a,b):
        if x.get('selectedTag')!=data['tag'] or x.get('selectedSort')!='最新':raise ValueError('DOM选中标签或排序不符')
    mode=e.get('mode')
    if mode=='switch':
        if old[:3]==two[:3]:raise ValueError('切换前后前3张未变')
        if previous is not None:raise ValueError('同标签已有批次，不能当新切换接续；断线或列表页丢了要重开，就新建采集目录（日期-重采N）从第一张重采')
        expected=two
    elif mode=='more':
        if previous is None or before['cards']!=previous:raise ValueError('加载前列表不是上一批确认列表')
        if len(two)<=len(old):raise ValueError('加载后卡片数量未增加')
        if two[:len(old)]!=old:raise ValueError('已读卡片变动或重排，必须停下报告')
        expected=two[len(old):]
    else:raise ValueError('证据mode必须switch或more')
    if [(p['companyName'],p['name']) for p in data['rows']]!=expected:raise ValueError('提交批次与稳定证据的新卡不一致')
    return b['cards']

