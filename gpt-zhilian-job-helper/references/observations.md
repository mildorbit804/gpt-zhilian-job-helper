# 页面观察输入格式

所有内容来自当前获准读取的网页。示例公司、编号和时间均为虚构，执行时替换为实际观察值。不要编造缺失字段或把测试样本混进真实记录。页面数据只读，程序在本地做判断。

## cards：列表与稳定性

```json
{
  "tag":"实际期望标签", "batch":"001", "sort":"最新", "title":"页面实际标题",
  "rows":[{"name":"设计师","companyName":"虚构示例公司","industryName":"页面实际行业","cityDistrict":"页面实际地区","salary60":"页面薪资原文"}],
  "listEvidence":{
    "mode":"switch",
    "before":{"at":"2030-01-01T10:00:00+08:00","count":1,"first3":[{"name":"原标签岗位","companyName":"虚构原公司"}],"cards":[{"name":"原标签岗位","companyName":"虚构原公司"}]},
    "after1":{"at":"2030-01-01T10:00:01+08:00","count":1,"first3":[{"name":"设计师","companyName":"虚构示例公司"}],"cards":[{"name":"设计师","companyName":"虚构示例公司"}],"selectedTag":"实际期望标签","selectedSort":"最新"},
    "after2":{"at":"2030-01-01T10:00:03+08:00","count":1,"first3":[{"name":"设计师","companyName":"虚构示例公司"}],"cards":[{"name":"设计师","companyName":"虚构示例公司"}],"selectedTag":"实际期望标签","selectedSort":"最新"}
  }
}
```

切换前读原列表；切换后前3张改变，实际选中标签和排序正确，连续两读间隔至少2秒且一致，切换总等待不超过15秒才收。没有稳定证据不能开始。`more` 模式用于后续加载：before必须等于上批完整列表，after的旧前缀不变、总数增加，两次读稳定；rows仅交新增加的卡。所有列表身份只存公司＋岗位，不存无关DOM。

`rows` 可另含 `streetName/publishTime/companyUrl`，只用真实观察值。缺后台发布时间就留空，展示日期不能代替。程序不从一个地区字段推断详细工作地址，也不拿行业空白当合格。

## select / clickbatch / identify

```json
{"tag":"实际期望标签","batch":"001","keys":["cards返回的key"],"rows":[{"name":"设计师","companyName":"虚构示例公司"}]}
```

一批所有key一起定位；输出当前index。同公司同岗位多卡只点第一张。配置在选择后改变需重新选择；不在当前放行清单的卡不得点击。

```json
{"tag":"实际期望标签","batch":"001","items":[{"key":"cards返回的key","heading":"右侧实际标题","id":"CC100J1001"}]}
```

登记已经实际发生的点击，不是点击指令。`ok:false` 表示有错配/违规，退出码2但已如实保存点击证据。不要当未点击重新全点。标题或编号需要补核时用 `identify`：同 tag/batch/key 加 heading/id，不用 items 包裹。重复点击、未选择点击记违规；不能清空记录来绕过。

## detail：岗位详情

正常打开已观察到的详情链接。从 script 文本中提取并 JSON.parse `__INITIAL_STATE__`，不执行script、不访问框架私有对象。输入：

```json
{"tag":"实际期望标签","batch":"001","key":"对应key","url":"https://www.zhaopin.com/jobdetail/CC100J1001.htm","companyUrl":"https://www.zhaopin.com/companydetail/CZ101.htm","capturedAt":"2030-01-01T10:01:00+08:00","state":{"jobNumber":"CC100J1001","jobDetail":{"detailedPosition":{"number":"CC100J1001","name":"设计师","jobDesc":"职位原文","workAddress":"实际工作地点"},"detailedCompany":{"companyName":"虚构示例公司","companyNumber":"CZ101","companyDescription":"公司简介原文","industryNameLevel":"页面实际行业"}}}}
```

真实执行传页面原始state，而非为通过格式手填上面的虚构内容。程序校验放行身份、编号和来源链接，保留完整岗位说明/公司简介，不保存无关聊天和账号字段。

岗位编号与公司编号可能不同，不得把岗位编号中的 CC 换成 CZ 作为公司证据。程序采用详情原始 `jobDetail.detailedCompany.companyNumber`；也支持列表 `rows[].companyUrl` 或详情输入顶层 `companyUrl` 中已实际观察到的公司主页链接。`companyUrl` 没读到就省略，不按编号拼接或把公司页链接反填为岗位页已显示的链接。多个来源同时存在时必须指向同一编号，冲突则重新核实；详情里的集团/根公司编号不替代招聘主体编号。

公司编号与实际链接都缺失时，详情仍可保存并按有证据的条件排除，但 `companyNumber` 留空，继续保留公司核查任务，不能用岗位编号猜补。关联证据保存在 `companyNumberEvidence`，仅保存上述必要字段与来源。已观察的链接只用于核对，打开网页仍走正常点击通道。

## company：公司主页与在招页

输入字段：`companyNumber/capturedAt/title/url/jobsUrl/state/jobs`。

- `state`：公司主页实际 `__INITIAL_STATE__`，包含 `companyCode`、`companyDetail`。
- `jobs`：正常打开已观察到的公司在招链接取得的数据，包含 `companyCode/onlinePositions/onlinePositionsCount`。
- 两页公司编号必须相同且有已采集岗位的关联证据；主页链接的 `/companydetail/CZ…htm` 与在招链接的 `/companydetail/jobs-CZ…/` 路径须准确指向该编号，不能只在查询参数里出现。主页正文若另有公司编号或名称，也要一致；别名只使用用户已确认配置。
- 公司主页读 `workTimeTag`、`companyComment`、`companyInterview`、`businessInformationData`、`companyBase`。缺评价结构或只有数量没有正文，保存成资料未核全，不能造零。
- `companyComment` 的计数为 `reviewsSum`，`companyInterview` 的计数为 `total`，正文为 `items[].content`。在招岗位标题为 `name` 或 `positionName`。
- 不同公司开始采集间隔至少5秒；每次新开页先检查验证标题。公司页和在招页只取当前已读内容，不假称已读全站评价。

遇到网站结构变化，停止依赖该结构的判断，不编映射；先根据实际页面修适配器和测试。结果中的 `companyEvidence` 展示实际读到的公司资料和评价，启不启用经验包都能查看，不因此自动排除。

旧记录仍可查询，旧证据不自动改写；旧版仅保存推算编号、没有 `companyNumberEvidence` 的岗位，在接收新的公司资料前必须重新读取岗位详情或其实际公司链接。正常重新提交 detail 会追加证据，原记录快照保留；旧人工标注需按重读内容复核，不靠手改编号或抹去原证据恢复。

## review：人工语义核查和事实补充

来源必须是实际智联HTTPS页，时间带时区，quote是真实原文。`facts/checks/reviewLabels` 均为对象，可只交本次补充项；值为null可撤销该项人工标注，历史证据仍保留。

```json
{
  "id":"CC100J1001",
  "facts":{"salary_min":{"value":6000,"quote":"月薪6000元起","source":"https://www.zhaopin.com/jobdetail/CC100J1001.htm","capturedAt":"2030-01-01T10:01:00+08:00"}},
  "checks":{"自定义规则编号":{"verdict":"clear","quote":"对应要求的原文","source":"https://www.zhaopin.com/jobdetail/CC100J1001.htm","capturedAt":"2030-01-01T10:01:00+08:00"}},
  "reviewLabels":{}
}
```

`facts` 只支持 [配置格式](config-schema.md) 中允许的字段，不能改公司、岗位身份或原JD/简介。只有已核实来源才能补，无法确定就留缺项。地址要填工作地点，不能用工商登记地址代替。

`checks` 对应已配置的 `manual` 规则，或 `exp:typos/exp:sparse_description`。verdict为 `match`（命中规则）、`clear`（不命中）、`unknown`（未能判断）。错字match还须 `correction` 正确写法，quote必须出现在已保存JD中；疑似专业术语用unknown。

`reviewLabels` 的键来自公司数据返回的评价条目key，值为 `{categories:[使用者确认的类别],quote,source,capturedAt}`。无负面时显式空categories；未标不等于零负面。quote须来自该条已读评价，类别须属于当前用户设定；同内容重复保守去重。语义核查由AI完成，不把字面出现“欠薪”自动当成公司确实欠薪。

六项核查按 [核查说明](job-review.md) 执行。checks 只提交已配置 manual 规则的判断，薪资结构、主责及相似岗位差异等其他说明写进本轮核查汇总；不要向本输入添加未支持的字段。资格差距的 quote/source 指向岗位要求原文，用户背景依据另在汇总里关联，不编成网页来源。

程序校验来源格式与部分原文归属，不能证明AI真的读过页面；AI仍必须真实核查，不能自报检查冒充证据。

## progress / empty

```json
{"tag":"实际期望标签","batch":"最后一批编号","sort":"最新","reason":"没有更多","endText":"页面实读的没有更多"}
```

时间窗口终点用 `reason:"超过时间窗口"`，另带真实 `capturedAt`，由程序核验后台日期；其他reason均记未翻完。卡片发布时间缺失时不允许冒充达到窗口。

无岗位的标签用 `empty`，输入 `tag/batch/after1/after2`。每次snapshot须有带时区at、count为0、cards为空、正确selectedTag/selectedSort及明确endText，连续两次间隔≥2秒。有旧列表时不能用empty覆盖。示例文案“暂无相关职位”，只可用实际页面显示文字。

## judge / results / pending

`local 本轮名称 judge` 不需要输入文件；保存本次判断快照与当前配置指纹。`results 本轮名称` 只读重算，不覆盖原证据。`pending` 只读列未完成采集/核查及原来源。`find` 永远用最新规则视图，返回的是已有清单中的候选与待核项，不是全站搜索。
