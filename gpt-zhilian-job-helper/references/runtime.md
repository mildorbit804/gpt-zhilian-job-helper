# 公开版程序与执行通道

本包附带 `scripts/zhilian.py` 及配套模块、隔离测试。程序负责本地配置、初筛、核查判断、已投去重、定位、屏蔽词、续查和事务备份；AI 仍通过宿主浏览器工具正常点击、滚动和读取页面，再把返回值交给程序。程序不联网、不自动投递、不读取聊天，不依赖其他项目或外部脚本。

运行环境：Python 3.9 或更新版本，macOS/Linux 标准库，无额外安装依赖。文件锁采用系统 `flock`，Windows 原生环境尚不支持；没有可用运行环境时如实说明，不自行安装。真实网站结构可能变化；本地测试通过不等于网站全流程已验证。

## 1. 首次启动

先确定**使用者自己的数据目录**，须位于技能目录之外。下面 `<技能目录>` 与 `<用户数据目录>` 是执行时替换的路径，不是要求用户手写命令；AI 根据实际路径调用。

```text
python3 <技能目录>/scripts/zhilian.py --project <用户数据目录> init
python3 <技能目录>/scripts/zhilian.py --project <用户数据目录> config show
python3 -m unittest discover -s <技能目录>/tests -v
```

新建状态的行业/地区规则、别名、屏蔽词和经验包均为空；标签待从页面读取。已有目录不覆盖。不从其他人的日志、技能备份或相邻项目导入配置。正式初次使用前跑随包测试，失败先修复或报告，不跳过。

主文件为用户目录中的 `zhilian-state.json`，每次修改先备份到 `backups/`，以文件锁和原子替换防并发覆盖。只读命令不修改状态。不要把用户目录、备份和实际配置打包发布。

## 2. 设置使用者自己的要求

按引导先读网页、集中确认自己的要求，**最后**问经验包。AI 把已确认内容写成配置 JSON；格式与可支持字段见 [配置格式](config-schema.md)。

```text
python3 <技能目录>/scripts/zhilian.py --project <用户数据目录> config validate 配置.json
python3 <技能目录>/scripts/zhilian.py --project <用户数据目录> config apply 配置.json --confirmed
```

`--confirmed` 是调用时记录“已有明确授权”的标记，不要求用户说固定口令，不是工具替用户作决定。没有授权不传；不得从沉默推断。配置采用完整替换，先读当前配置再改对应项，不丢弃其他已有选择。网页设置仍由用户决定是否更改，程序只记录并核查。

经验包常用操作：

```text
… experience catalog
… experience preview capital
… experience set capital --confirmed
… experience set span --options 参数.json --confirmed
… experience remove span --confirmed
… experience clear --confirmed
```

此处及下文 `…` 代表完整的 `python3 <技能目录>/scripts/zhilian.py --project <用户数据目录>` 命令前缀，不能把省略号直接执行。`catalog/preview/show` 只读；`preview` 会对已有清单显示前后变化，不保存。`set all --confirmed` 仅在用户明确整包复用后执行。`remove/clear` 删除经验设置，保留用户独立规则。

例如将时长改为10小时且仅提醒，参数文件为 `{"threshold":10,"action":"remind"}`。修改或撤销后，`results`、`find` 和后续初筛自动读取最新配置，不改原始证据、不写永久拒绝。已核查缓存随配置/名单/词表变化失效；此前被拦但尚未读详情的卡会重新列为待采集，不能只重算已读岗位就宣称全部恢复。

## 3. 正式初筛前同步投递名单

正常打开个人中心投递记录，分页核全公司＋岗位并核对页面总数；每条记录上显示的投递日期一并读取。不打开聊天、记录详情或消息，不读取反馈正文。输入只允许必要字段：

```json
{"source":"https://i.zhaopin.com/","capturedAt":"2030-01-01T10:00:00+08:00","total":2,"rows":[{"company":"虚构示例公司","job":"设计师","appliedAt":"2029-12-20"},{"company":"虚构示例乙公司","job":"文员"}]}
```

示例时间须替换为真实采集时间。`appliedAt` 只填页面显示的日期，页面没显示就不写；不编岗位编号、投递日期或HR反馈。历史补录不算今天新投。

```text
… applied sync --input 名单.json --confirmed
… run start 本轮名称
```

每轮 `run start` 必须有新的完整同步记录，名单不能早于24小时或明显来自未来，不能把上轮同步重复当本轮同步。公司名按格式与组织后缀规范化，岗位支持行政人事词序与编号后缀；品牌和法人主体差异大时仅使用用户确认的别名组，不凭包含关系把整个公司或无关主体排除。支持 `applied list`；用户亲自发送并明确告知后用 `applied add --input 单条.json --confirmed` 记账，可选 `appliedAt` 只能填真实时间。

默认 `applicationPolicy.reposts=remind` 时，投过的岗位在投递之后重新发布（卡片发布时间晚于最近一次投递）时，初筛放行、照常核查；符合全部要求的，`results` 的 `repostReminders` 给出提醒原句，`repeat list --run 本轮名称` 只读查看。使用者说不投用 `rejected add`，再投并告知后用 `applied add`；使用者告知漏记的日期用 `applied annotate`。详见 [投过的岗位重新发布](repeated-applications.md)。

## 4. 浏览器采集 → 本地处理

每次从工具返回值保存 JSON，再调用：

```text
… local 本轮名称 cards --input 批次.json
… local 本轮名称 select --input 选择.json
… local 本轮名称 clickbatch --input 点击结果.json
… local 本轮名称 detail --input 详情.json
… local 本轮名称 company --input 公司.json
… local 本轮名称 review --input 人工核查.json
… local 本轮名称 progress --input 终点.json
… local 本轮名称 judge
```

完整输入结构见 [采集格式](observations.md)。`zlocal` 是 `local` 的别名。

- `cards`：保留当前标签和完整列表稳定证据，初筛按当前配置、已投、明确拒绝及标签词表执行。同公司同岗位重复只放行第一张，跨标签仍保留来源。
- `select`：一次提交本批所有放行 keys 和当前完整公司/岗位列表。任一卡零匹配整批不登记；返回第一张匹配卡的当前 index，不用历史位置。
- 按宿主允许的点击能力逐张点击，确认右侧标题对应卡片后读取详情链接编号；一次 `clickbatch` 登记整批。未 select、清单外、重复点击留下违规记录；标题/编号错配不认可，重读后用 `identify` 补核。不能用只读页面脚本偷偷点击。
- `detail/company`：读取已观察到的链接和页面已提供的 `__INITIAL_STATE__` JSON，不执行页面脚本、不读 Vue 私有状态、不 fetch。输入经过白名单转换，丢弃与职位核查无关的聊天、账号等字段。
- 岗位关联公司时使用详情实际公司编号及已观察的公司主页链接，不从岗位编号推算；多来源冲突时暂停关联。未读到公司编号或链接的详情可保留，但不能凭猜测接收公司资料。旧推算记录补采公司前先重读详情，具体字段和兼容处理见采集格式。
- 详情和公司分批开关，一批最多5个新标签，保存结果后关闭自己开的这批，列表页保留。不同公司间隔至少5秒；验证立即停，登录由用户本人处理。
- 公司评价保留实际条目、数量和覆盖范围。数量有但内容缺失不是零条；语义分类通过 `review` 绑定实际原文，不能仅按低分判定。公司在招只读取当前页，报告覆盖范围。
- `review`：为程序无法可靠提取的字段补带来源的事实，或提交已读JD/评价的人工判断。未知字段、无来源、错别字不在JD原文、评价不属于已读条目时拒收。不得编事实补齐配置。
- 空标签使用 `empty`，两次稳定读确认当前标签/最新排序、0张卡片与明确空列表文案。加载中或空白屏不能当没有岗位。

## 5. 深度、完成与续查

时间窗口来自用户配置，`days:null` 表示翻到明确“没有更多”。有时间窗口时，仅用带时区的后台 `publishTime` 检验尾部20条；疑似凌晨刷新剔除后至少10条、过半超过窗口才可结束。日期缺失继续至明确终点，不为凑日期额外点已排除卡片。网站混排/证据异常时如实标未完成。

`progress` 必须属于该标签最后一批。后续新增卡片会使旧终点失效；切换网页条件/标签/时间窗口需另开采集，旧目录留证。

```text
… results 本轮名称
… pending
```

结果分别给 `scanComplete`（本轮范围终点齐全、应读取的详情及公司资料已处理且无违规）和 `reviewComplete`（已读结果没有尚待执行的规则核查）。仍保留的岗位须继续补齐证据；`reviewRequired` 表示仍需AI在交付前复核，任何标记都不授权代投。网页条件改变时 `scopeChanged:true`，不会声称当前条件已扫完。

当前规则已给出有依据的排除结论时，该岗位正常结案，不再为它补查无关公司资料或其他规则。`pending` 只列仍需执行的核查；排除岗原先未核的项目保存在 `notRequired` 中，含义是“因已排除，无需继续”，不能写成全部通过。排除原因、来源和原始证据照常保留，未读全的评价仍标明覆盖不足。已投、用户拒绝或全部来源标签均被标题词屏蔽的岗位也不进入当前续查；只屏蔽一个标签不影响其余标签。

结案是按当前规则计算的结果，不写成永久拒绝。撤销排除条件、改为仅提醒、提高阈值使原排除失效或撤销屏蔽后，未完成的公司资料及规则核查会自动恢复。只有执行 `judge` 留下判断且当前规则版本相同，已排除结论才可在后续采集中复用；只读过详情、缺证据又没有排除结论的仍须续查。列表未到底、未读放行卡、违规点击和范围变化照常阻止扫描完成。

仅采集详情、公司未核完的不会按“看过”跳过。重采时新建名称，`pending` 给原来源、key和缺项；新一轮补核成功并 `judge` 后，旧的同岗位续查项可被解决。未再次出现也不能静默丢弃，应按原证据核实或报缺项/下架。

## 6. 定位与屏蔽词

```text
… find --company "X公司" --job "设计师" --run 本轮名称
… find --job "数据分析师"
… find --batch 条件.json --run 本轮名称
```

条件文件：`{"queries":[{"company":"X公司","job":"设计师"},{"company":"Y公司","job":"产品经理"},{"job":"数据分析师"}]}`，名称均为虚构示例。最多3个独立条件，返回所有匹配，合并重复并标 `matchedConditions`，每项给数量。没有匹配也逐项报0。默认最近更新的采集清单，返回来源日期、范围、扫描/复核状态；不会搜索网站或改记录。结果使用当前规则，已投/拒绝/被规则排除项不返回。

```text
… keywords summary --run 本轮名称
… keywords preview --tag "实际标签" --word "用户考虑的词" --run 本轮名称
… keywords add --tag "实际标签" --word "用户确认的词" --confirmed
… keywords list
… keywords remove --id 规则编号 --confirmed
```

`zfind/zblock` 为别名。预览只读，统计来源清楚；只过滤指定标签内的标题，撤销可恢复其他条件仍通过的条目，不改已投/永久拒绝/已看记录。用户拒绝单个岗位用 `rejected add/remove --input 单条.json --confirmed`，公司＋岗位范围不扩大。

## 7. 接力与证据

```text
… --actor 执行者 relay claim --message "本次任务"
… --actor 执行者 relay step --message "实际进度"
… --actor 执行者 relay release --message "未完成部分和下一步"
```

注意全局参数 `--actor` 放在子命令前。接力占用时其他执行者不能写入，不自动超时夺锁，不自动派活。所有读写受文件锁保护，外部修改检测到就拒绝覆盖。违规点击阻止正常 release；先如实报告，不能删除证据或换目录假装没有发生。如实告诉使用者后，用下面的命令登记报告内容，登记后可以 release；记录保留，该轮仍不算扫描完成，需要时新建采集重扫。

```text
… violations list --run 本轮名称
… --actor 执行者 violations ack --run 本轮名称 --message "已向使用者报告的内容"
```

历史证据与每次判断保存在本轮 `evidence` 中，当前视图动态计算；配置修改不改旧证据。查询不产生新状态、备份或永久判断。公开包仅含代码、文档和虚构测试，不包含任何用户数据。


## 8. 本人的已有历史迁移

仅在使用者授权迁移自己的本地历史时，执行 [历史记录导入](history-import.md)。导入有字段白名单、事务备份及重复导入保护，不依赖其他程序，不把旧导入当作本次网页已投同步。
