# 配置格式（供 AI 执行）

用户用自然语言选择，AI 将选择转换为本格式；不要求使用者理解代码。完整配置用 `config show` 起步，改动前保留其他字段。未支持的选择用显式人工核查规则表达，不偷偷套其他人的默认值。

```json
{
  "version": 1,
  "tags": [],
  "scan": {"days": null, "sort": "最新"},
  "webpage": {},
  "rules": [],
  "experience": {},
  "companyAliases": [],
  "applicationPolicy": {"reposts": "remind", "reuseReviewed": false}
}
```

- `tags`：从页面读到且经使用者确认的三个标签；初始为空，开始扫描前必须填实际名称。
- `scan.days`：用户明确的正整数窗口；null表示翻到没有更多，不能把示例天数当默认。
- `webpage`：实际确认的网页条件及所选值，包含城市、薪资、学历等需要记录的选项；读不到的标明未核实，不填成不限。此对象记录采集范围，不会自动点击网站筛选，也不会将网页选项擅自转为JD硬规则；网页筛选已生效后仍按 `rules` 复核使用者额外要求。
- `rules`：用户独立要求；初始为空，每项字段固定为 `id/field/op/value/action`。
- `experience`：只放明确选中的经验项；拒绝或撤销时删除相应项，不保留默认阈值。
- `companyAliases`：用户确认同一主体的名称组，例如 `[["虚构示例公司","虚构示例品牌"]]`。不是全站品牌推断，不能把不同法人自动并成一家。

## 自定义规则

`action` 为 `exclude`（命中就排除）或 `remind`（命中只提醒）。偏好不得转成硬排除。字段缺证据时标待核，不当符合也不直接排除。

| 字段 | 值 | 支持比较 |
|---|---|---|
| `title/company/industry/district/street/work_address/description/company_intro/employment_type/education/rest_schedule/company_type` | 字符串 | `contains_any` 包含任一、`not_contains_any` 不含任何、`in` 等于任一、`not_in` 不等于任何；规则 value 为非空字符串列表 |
| `capital_cny/work_span_hours/net_work_hours/start_hour/end_hour/salary_min/salary_max/experience_years/company_size/company_age_years/commute_minutes` | 非负数字 | `gt/gte/lt/lte/eq`，规则 value 为数字 |
| `manual` | 人工核查任务 | `op:check`，规则 value 为使用者要求的文字；用 review 提交是否命中及证据 |

数字单位：资本为人民币元；时间/跨度为小时；薪资为人民币每月金额；经验、公司年龄为年；规模为人数；通勤为分钟。来源单位不明不转换。月薪结构/奖金等需逐项核清，不能把年包或日薪直接塞成月薪。工作地址不等于登记地址。对于学历层级、经验是否匹配、行业语义等复杂判断，优先人工规则，不把字符串相等误当完整判断。

示例（仅演示格式，无默认行业/地区值）：

```json
{"id":"duration","field":"work_span_hours","op":"gt","value":10,"action":"remind"}
```

程序会从明确的岗位和公司作息解析同日上下班跨度，多组冲突/跨日/中文模糊表述标待核；不自动扣午休。资本须有明确人民币单位才能换算。薪资、学历、经验等页面字段尚未建立可靠统一解析时，用 `review.facts` 补带原文来源的标准化事实；不支持的语义要明确需要AI核查。

## 经验包参数

`experience catalog` 给可选模板，默认没有任何启用项。命令 `experience set 项目 --options 文件 --confirmed` 会将明确修改值与该项模板合并，再完整校验；未识别字段拒收。

| 名称 | 选用时的模板 |
|---|---|
| `capital` | `{"action":"exclude","threshold":500000}`，小于等于该人民币金额 |
| `span` | `{"action":"exclude","threshold":9}`，每日在岗跨度严格大于该小时数 |
| `mandatory_overtime` | `{"action":"exclude"}`，明确强制加班措辞 |
| `residential_address` | `{"action":"remind"}`，登记地址住宅特征只作提示 |
| `hiring_structure` | `{"action":"remind","sizeBelow":20,"rolesAtLeast":5}`，小规模同时招多个文职，只用已读页；不称为空壳 |
| `sparse_description` | `{"action":"remind"}`，须AI判断资料是否不足并提交原文 |
| `reviews` | `{"action":"remind","categories":[],"threshold":1,"mode":"combined"}`，默认只展示；排除前须设置类别、正整数阈值及合并/分别计数（combined/separate） |
| `typos` | `{"action":"exclude"}`，须AI提交明确错字的JD原文与正确写法；疑似用unknown |

“整包复用”只采用表内项目，负面评价仍仅提醒，直到使用者明确类别和自动排除标准。整包不附任何行业/地区、薪资、学历、岗位性质或公司名单。

设为只提醒（remind）的经验项，遇到平台没提供的资料（如公司规模、注册资本、作息未显示，或评价没有全部露出），列为提醒并注明资料不全，不让岗位停在待核；设为排除（exclude）时缺资料仍为待核，由使用者核实。

修改和撤销后即时重算当前结果。未读取详情的旧拦截卡会重新进入续查清单，不能声称撤销已经恢复网站上所有未采集岗位。规则/经验匹配不会自动写入拒绝记录。


## 六项核查中的复合要求

薪资结构、实际职责、资格差距和语义上下文由助手按 [六项核查](job-review.md) 读取证据；只有涉及使用者已确认的筛选标准时才保存 rules。复杂语义使用 field:manual/op:check，不用简单字面 contains_any 代替。纯说明和相似岗位对照写在本轮核查汇总，不虚构 facts 字段、experience 模板或自动差异接口。

以下仅为虚构配置示例：假设使用者明确选择“学历、专业、对口经验这三类的一般及必须要求中，三个独立条件均不符时仅提醒，不计优先项”。这不是默认值：

```json
{"id":"qualification_gap","field":"manual","op":"check","value":"学历、专业、对口经验的一般及必须要求中，至少3项独立且有证据不符合时命中；不计优先/加分项、未知项或重复表述；不可缺少的资格另列。","action":"remind"}
```

读取 config show 后仅增改目标规则，保留其余配置；阈值、计数范围和 action 必须替换为用户实际选择，不可照抄示例。核查汇总列每项 JD 原文和背景依据；达到阈值为 match，明确不会达到为 clear，尚不能判断为 unknown。已证实的不符数达到阈值时可以 match，同时披露仍未知的其他条件；不符数加未知数仍低于阈值时可 clear，否则 unknown，不能默认通过。

review.checks 的 quote/source/capturedAt 记录实际岗位要求原文、智联页面和读取时间；背景依据在用户自己的核查汇总里关联已授权资料，不伪称背景来自招聘网页，不往 source 填私有文件路径。修改计数或动作后重新核对并提交 review，再运行 judge/results；撤销直接删除对应规则，其他规则仍有效。


## 历史记录与重发策略

`applicationPolicy` 可省略以兼容旧配置，省略时等同 `{"reposts":"remind","reuseReviewed":false}`。

- `reposts:remind`：保持原有重发核查及提醒流程；`skip`：已投同公司同岗位无论发布时间或编号是否变化都跳过，不产生重发提醒。只影响该岗位，不扩大为整家公司。
- `reuseReviewed:false`：历史已核结论仅存档；`true`：复用经授权导入且 `completed:true` 的已核身份跳过。仅看过及 `completed:false` 不跳过；修改筛选条件后是否仍复用由使用者选择，旧结论不能称为新规则下已核查。
- `district` 和 `street` 分别来自原卡片 `cityDistrict`、`streetName`；缺失保持未知。二者不等于精确工作地址，不与工商地址混用。
- 公司简称或不同主体不能仅凭包含关系自动合并；只按配置中已确认的别名组对齐。历史缺名称的记录保留编号，不发明身份。
