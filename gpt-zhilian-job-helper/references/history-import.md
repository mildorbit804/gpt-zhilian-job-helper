# 导入使用者本人的已有记录

用户授权迁移本人的本地记录后，读取指定来源并在技能包外保存原始副本；只提取以下白名单字段。不得读取其他人的项目，不把用户数据写入共享技能。导入器只处理明确提供的文件，不搜索电脑。

```text
python3 <技能目录>/scripts/zhilian.py --project <用户数据目录> history import --input <历史导出.json> --confirmed
python3 <技能目录>/scripts/zhilian.py --project <用户数据目录> history summary
```

```json
{
  "version": 1,
  "source": "使用者授权的历史导出",
  "seen": ["真实已有编号"],
  "reviewed": [{"id":"另一真实编号","company":"虚构示例公司","job":"设计师","decision":"历史核查结论","completed":true}],
  "applied": [{"company":"虚构示例公司","job":"设计师"}],
  "rejected": [{"company":"虚构示例乙公司","job":"文员","reason":"使用者明确不考虑该岗位"}]
}
```

上例名称与编号仅示意，执行时只能填来源中真实存在的值。`reviewed` 可只有 id/decision/completed，但不能猜公司和岗位；公司与岗位要成对。`completed` 必须明确为布尔值，未核完用 false。已投和拒绝必须有公司＋岗位。可选 `appliedAt` 只取真实、无歧义日期，不用导入日期代替。未提供时间则省略；不导入招呼语、HR信息、聊天/反馈正文。

- `seen` 仅存档，绝不据此跳过核查；`reviewed` 是否复用由 `applicationPolicy.reuseReviewed` 控制，默认 false，且只复用明确 completed=true 的记录。
- `reposts=skip` 是使用者要求已投始终跳过时的选项；默认仍为 remind。公司＋岗位匹配不扩为整家公司。
- 同一导出重复导入不重复记账；已有同身份投递/拒绝不覆盖，历史已核按真实编号保留。第一次合并前自动备份。
- 缺名称记录只能按真实相同编号识别；列表未提供该编号时不能保证初筛跳过，迁移报告必须披露。不同名称主体未确认时列差异，不自动加别名。
- `pending` 会单列历史缺身份或未核完记录，标明不是当前新岗位候选；缺项不能在迁移后静默消失。
- 核对 seen/reviewed/applied/rejected 的源数量、导入数量、重合数量与无法识别数量。缺项原样保留，不能为追求数量相等而造记录。
- 本命令不设置 `appliedSync`，不计为今日投递，不使任何扫描自动完成。正式扫描仍要读取当轮投递名单并核对总数；不能把迁移时间充作网页采集时间。
