# 小满长期认知图谱实施计划

**依据：** 本轮用户确认的「小满长期认知架构 · 第一版」；同项目[定位草案](../specs/01-background-and-goal.md)不作为此次功能的前置决策。
**目标：** 收藏和获准使用的对话能改变有出处的长期判断；删改源材料即停用衍生判断；聊天只装载相关的有效节点。
**架构：** SQLite 同事务保存来源和任务；后台抽取、提炼，后端校验并增量写图。图谱提供四种认知视角，图推理和特性装载只读。
**技术栈：** 现有 Python、FastAPI、SQLite、LLM、pytest 与原生 JavaScript；不新增依赖。
**执行环境：** 当前工作树有用户变更，保留未涉及的修改；只对临时测试数据库执行验证。

- [x] AC-01：收藏和允许成长的对话登记可重试任务，重启不丢；默认对话不进入长期成长。以 API 和真实 SQLite 测试验证。
- [x] AC-02：抽取证据要求原文可定位；逐条提炼并记录支持、反例和修订关系。用户记忆只从显式确认写入；荣格倾向需不同输入支持。用假模型验证，且不引入主体强度和情绪。
- [x] AC-03：编辑、删除或清空时原子停用依赖节点，用剩余来源重算；旧任务不能重新写回。以阻塞模型的并发回归测试验证。
- [x] AC-04：按话题读取有效子图，分块注入模型；缺少匹配不全量兜底。现有想法面板兼容展示，补齐用户记忆和对话来源。
- [x] AC-05：用 `python -m pytest -q` 验证全量回归，`node --check static/pieces.js` 校验客户端。旧 `piece_mind` 表保留，已有收藏排队重提炼，不把无版本依据的快照直接装载；未授权的旧聊天不回填。回退代码前备份数据库；回退版不会读取新增表。

## 验证记录

- 初始两条端到端测试因旧接口没有 `graph` 字段而失败；实现后通过。全量 pytest 为 142 项，包含迁移、重启、批处理、修订链、来源并发删除、授权和记忆验证。
- `node --check static/pieces.js`、移动尺寸无头 Chromium 界面冒烟和 `git diff --check` 通过。冒烟使用模拟 API，不访问真实模型。
- OMC 静态扫描 Python Ruff 无发现；JavaScript 未配置仓库 ESLint，不视为全量静态扫描通过。未新增工具链配置。
- 适用的工程约束：common/Python 用于事务、参数化 SQL、异步取消、模型输出校验及迁移测试；JavaScript 用于请求边界、安全文本渲染和界面错误处理。使用当前仓库依赖，无 Java 或移动端原生改动。规则 SHA-256 见下。
  - `c067fc5658743e2aa3c5b06bc430db3e906baa7c961ea2a69f94cb3dcafdf582  common/coding-style.md`
  - `01ced86aaa34b5bcc77ada2b96774db03fafce7faf82928b1bbcdf65f40b4325  common/patterns.md`
  - `0aaf8514704088e231ab8e515c3902c3cf09369d711ecf330338b5cf613bd72a  common/security.md`
  - `b4626c78d6fad71ac500ad29f03c65fbfe2aea55a3b39b15357454efe01b2c72  common/testing.md`
  - `8ab6d4cfd0bfa22306ac93dad4d13c12f202afc6906485d7052958ca364e27d7  python/coding-style.md`
  - `190989b5541bf6ccef70cdea33167e88540c46ab2285dd100fdf4b85b144513e  python/patterns.md`
  - `ec82f1ca3612a2f1c1983bfaeba2fa0e9453fba4e8d246d6b3170b5919ad1154  python/security.md`
  - `76004b2939d5bfae7f76988a45f3c6fece5d0c90263bbdc3d72b7b5aeaec131a  python/testing.md`
  - `d729e2d9091fc27995259080d08b2cf8dad56b3f23da617e109e8d1ffedb834f  typescript/coding-style.md`
  - `d41244e0a8aad887024937344ef68a49f8a1d31fceefd1bf00c892166c372b2b  typescript/patterns.md`
  - `47f6ae12dcca031fe49a9ca43202286ca0afa3a831774d06f4c9cda261e4b4e9  typescript/security.md`
  - `3f93a2b7782455306f143e77a7f9ac56a31f814ea4ce40e96ab2fa1e11393ed4  typescript/testing.md`

- 独立后端审查的三项初始发现均已修复、复核并新增回归：历史回复间接带入已失效特性、提炼未记录模型可见的旧判断依赖、非法 Unicode 导致任务未进入持久化重试。针对复核发现的跨主题误失效，已改为分主题提炼并通过回归。未连接真实模型验证生成质量。
