# 技术说明

这份文档讲怎么运行、怎么配置、各部分怎么实现。产品功能和效果展示在 [README](../README.md)。

## 目录

- [快速开始](#快速开始)
- [配置](#配置)
- [数据存在哪里](#数据存在哪里)
- [HTTP 接口](#http-接口)
- [Agent 能用的工具](#agent-能用的工具)
- [错误码](#错误码)
- [项目结构](#项目结构)
- [测试](#测试)
- [重新录制动画](#重新录制动画)
- [已知限制](#已知限制)
- [功能实现细节](#功能实现细节)

## 快速开始

需要 Python 3.10 及以上。

```bash
pip install -r requirements.txt
cp .env.example .env          # 填入 LLM 和 TMDB 的配置，其余可以先空着
python -m uvicorn app.main:app_factory --factory --port 8000
```

打开 http://127.0.0.1:8000 。页面右下角有四个状态点：LLM、生图、TMDB、搜索。绿色表示已配置。

最少需要配置两项就能用日记和对话：

1. `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`：任意支持 tool calling 的 OpenAI 兼容网关。
2. `TMDB_API_KEY`：用来搜片、取封面和剧照。在 https://www.themoviedb.org/settings/api 申请。

没配置的功能不会让服务起不来。用到时会返回带错误码的明确提示，例如 `tmdb_not_configured`、`image_not_configured`。

服务没有登录认证，只监听本机。要给同事用，请放在内网并自行加访问控制。

## 配置

复制 `.env.example` 为 `.env`。所有项都是环境变量，进程里已有的环境变量优先于 `.env`。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LLM_BASE_URL` | 空 | 对话模型网关，OpenAI 兼容，必须支持 tool calling |
| `LLM_API_KEY` | 空 | 对话模型密钥 |
| `LLM_MODEL` | 空 | 模型名 |
| `IMAGE_BASE_URL` | 沿用 `LLM_BASE_URL` | 生图网关，调用 `POST {IMAGE_BASE_URL}/images/generations`，返回 `b64_json` 或 `url` 都支持 |
| `IMAGE_API_KEY` | 见下 | 生图密钥。只有在没单独设 `IMAGE_BASE_URL` 时才会沿用 `LLM_API_KEY`，不会把对话密钥发给另一个主机 |
| `IMAGE_MODEL` | 空 | 生图模型名 |
| `IMAGE_SIZE` | `1024x1536` | 生图尺寸 |
| `IMAGE_TIMEOUT` | `600` | 生图最长等待秒数 |
| `TMDB_API_KEY` | 空 | TMDB 的 v3 key 或 v4 read token 都行 |
| `TAVILY_API_KEY` | 空 | 联网搜索。不填则用 DuckDuckGo，不稳定；填了用 Tavily |
| `POSTER_FONT` | 空 | 海报字体路径，需含中文字形；不填则自动找系统字体 |
| `DATA_DIR` | 项目根目录下的 `data/` | 数据库和图片的存放位置 |
| `MAX_AGENT_STEPS` | `6` | Agent 单次回复最多连续调用几轮工具 |

如果公司的生图接口格式不同，只需改 [app/posters.py](../app/posters.py) 里的 `OpenAICompatImages`。

联网搜索用 DuckDuckGo 时，页面可能被限流或返回空结果，会报 `search_no_results`，建议配 Tavily。

## 数据存在哪里

默认存在 `DATA_DIR`（不设就是项目根目录下的 `data/`）：

```
data/
├── diary.sqlite3      日记、对话、海报、收藏、诗，全部在一个 SQLite 文件里
├── posters/           台词海报 PNG
└── clips/             上传的截图 JPEG
```

五张表：

| 表 | 内容 |
|---|---|
| `entries` | 电影条目：TMDB 编号、片名、年份、导演、简介、类型、评分、感受、标签、观影日期、状态（watched 或 wishlist） |
| `messages` | 对话。`entry_id` 为空表示全局对话；每条记录附带来源链接和用到的工具 |
| `posters` | 海报：台词、意境、提示词、文件名，以及生图失败时的降级原因 |
| `clips` | 收藏的台词和截图 |
| `poems` | 保存的诗：诗人、标题、正文 |

删除一部电影会级联删除它的对话、海报、收藏和诗。删除电影或海报时，对应的图片文件也会一并删掉。

备份就是复制整个 `DATA_DIR`。

## HTTP 接口

页面本身就是这些接口的客户端，你也可以直接调用。出错时统一返回 `{"error_code": "...", "error": "原因"}`。

| 方法与路径 | 作用 |
|---|---|
| `GET /api/status` | 各项是否已配置 |
| `GET /api/movies/search?q=&year=` | TMDB 搜片 |
| `GET /api/entries` | 全部条目（含想看） |
| `POST /api/entries` | 新增。传 `status: "wishlist"` 即想看 |
| `GET /api/entries/{id}` | 单条详情，带海报、收藏、诗 |
| `PATCH /api/entries/{id}` | 改评分、感受、标签、日期、状态 |
| `DELETE /api/entries/{id}` | 删除 |
| `GET /api/on-this-day?on=YYYY-MM-DD` | 往年同月同日看过的 |
| `GET /api/chat`、`POST /api/chat`、`DELETE /api/chat` | 全局对话：读取、发送、清空 |
| `GET/POST/DELETE /api/entries/{id}/chat` | 单片对话 |
| `POST /api/entries/{id}/posters` | AI 生图海报，body `{"quote": "..."}` |
| `POST /api/entries/{id}/collage` | 剧照拼贴，body `{"quotes": ["...", ...]}`，1 到 4 句 |
| `GET /api/entries/{id}/quote-candidates` | 从 Wikiquote 查台词候选 |
| `DELETE /api/posters/{id}` | 删除海报及文件 |
| `GET /api/clips?kind=quote\|shot` | 全部收藏 |
| `POST /api/entries/{id}/clips` | 收藏台词 |
| `POST /api/entries/{id}/clips/shot` | 上传截图 |
| `DELETE /api/clips/{id}` | 删除收藏 |
| `GET /api/poem-styles` | 22 位诗人 |
| `POST /api/entries/{id}/poem` | 写一首诗（不保存） |
| `POST /api/entries/{id}/poems` | 保存一首诗 |
| `GET /api/poems`、`DELETE /api/poems/{id}` | 列出、删除已保存的诗 |

启动后访问 `/docs` 可以看到交互式的接口文档。

## Agent 能用的工具

| 工具 | 作用 |
|---|---|
| `search_movie` | 在 TMDB 按片名（可加年份）搜索，返回候选 |
| `web_search` | 联网搜索影评、解读、幕后故事，返回标题、链接、摘要 |
| `read_diary` | 读日记概要，或某部电影的完整记录；可按最低评分、状态（已看或想看）过滤 |
| `add_to_diary` | 加入日记，可带评分、感受、标签、观影日期 |
| `update_diary` | 更新评分、感受、标签、日期、状态 |
| `add_to_wishlist` | 放进想看清单，可带理由 |
| `save_quote` | 收藏一句台词（必须是原话） |
| `make_poster` | 把一句台词做成 AI 生图海报 |

Agent 不能直接调用剧照拼贴和一键成诗，这两个是界面上的功能。

## 错误码

每个错误都带稳定的 `error_code` 和人能看懂的原因，界面会原样显示，不会把失败伪装成成功。常见的：

| 错误码 | 含义 |
|---|---|
| `llm_not_configured`、`tmdb_not_configured`、`image_not_configured` | 对应服务没配置 |
| `llm_auth_failed`、`tmdb_auth_failed` | 密钥无效 |
| `llm_rate_limited`、`quotes_rate_limited` | 被限流，稍后再试 |
| `*_timeout`、`*_unreachable` | 超时，或连不上（`llm`、`tmdb`、`image`、`search`、`quotes`、`still` 都有） |
| `entry_already_exists` | 已在日记里 |
| `entry_has_no_tmdb_id` | 手动添加的电影没法拼贴 |
| `stills_not_found` | TMDB 没有可用剧照 |
| `still_unreachable`、`still_download_failed` | 剧照下载失败，多半是网络或代理拦了 `image.tmdb.org` |
| `poster_too_many_quotes`、`poster_quote_too_long` | 超出拼贴或 AI 海报的句数、字数上限 |
| `poster_font_missing` | 找不到中文字体，设 `POSTER_FONT` |
| `search_no_results` | 联网搜索没有结果 |
| `agent_step_limit` | Agent 工具调用用完了步数 |
| `image_too_large`、`image_decode_failed` | 上传的截图过大或打不开 |

## 项目结构

```
app/
├── main.py        FastAPI 应用、全部接口
├── agent.py       系统提示、工具定义、工具循环
├── llm.py         OpenAI 兼容的对话客户端
├── tmdb.py        TMDB：搜片、详情、剧照
├── websearch.py   Tavily / DuckDuckGo 联网搜索
├── posters.py     AI 生图海报：提示词、生图、文字排版、渐变降级
├── collage.py     剧照拼贴：版式、下载、排字
├── quotes.py      Wikiquote 台词查询与解析
├── poems.py       22 位诗人与成诗
├── clips.py       台词与截图的校验和存储
├── db.py          SQLite 表结构与读写
├── config.py      环境变量
└── errors.py      统一的错误类型
static/            前端：index.html、app.js、style.css（无构建步骤）
tests/             自动化测试
docs/demo/         录制动画用的脚本和替身后端
docs/media/        README 里的动画
design/            早期的界面设计稿
```

前端是原生 HTML、CSS、JS，没有构建步骤，改完刷新即可。

## 测试

```bash
python -m pytest -q
```

测试用假的 LLM、生图、TMDB、搜索和 Wikiquote，不联网、不花钱，覆盖：日记增删改与想看清单、Agent 工具循环与步数上限、工具报错透传、联网搜索解析、海报生成与降级、剧照拼贴的版式和限制、Wikiquote 解析、收藏与成诗。

## 重新录制动画

README 里的动画由脚本从真实界面录制，不是手工拼的。

```bash
node docs/demo/record.mjs            # 录全部场景
node docs/demo/record.mjs poem       # 只录一个：calendar、add-rate、agent-chat、wishlist、quote-collage、poem
```

需要 Edge 或 Chrome（路径不同时用环境变量 `BROWSER` 指定）、`ffmpeg`，以及已装好项目依赖的 `python3`。

- 每个场景启动一个**独立的演示后端**（[docs/demo/demo_server.py](demo/demo_server.py)）和临时数据目录，录完即删，**不会碰你真实的日记**。
- 演示后端用的是真实的前端和真实的接口，只把模型、TMDB 查询、搜索和 Wikiquote 换成脚本化的替身。因此录制不需要任何密钥，也不消耗额度。
- 封面和剧照用的是 TMDB 的真实图片地址，存在 [docs/demo/tmdb_fixture.json](demo/tmdb_fixture.json)。所以录制时本机必须能访问 `image.tmdb.org`，访问不到时脚本会在开头报错，不会录出一堆空封面。
- 动画和示例海报里出现的电影海报和剧照，版权属于各自的片方，仅用于演示本工具的界面。
- 每个场景结束时会用接口核对结果（比如“电影已入库、4 星、有感受”），核对不过就报错，不会输出一张看着对、数据不对的动画。
- 画面里的光标和字幕条是录制时叠上去的，页面本身没有。

## 已知限制

- 没有登录和多用户，只适合本机或内网自用。
- 剧照拼贴的剧照选择是固定的：同一部片、同样的句数，总是同一批画面；裁切不识别人脸，人物偏在边缘时可能被切掉一部分。
- 4 句都接近 120 字时，22 号字也可能放不下，文字会压进剧照区。正常长度不会遇到。
- 联网搜索默认走 DuckDuckGo，经常被限流。想稳定请配 Tavily。
- 台词海报的 AI 生图依赖外部模型，速度和效果取决于你的网关。
- 想看清单和已看共用同一个唯一键（TMDB 编号），所以同一部片不能同时出现在两处。

## 功能实现细节

README 里每项功能背后的规则、数值和限制。

### 自动配色的规则

自动配色的规则：取所选月份内、日期不晚于该月最后一天、最近看过的 5 部（想看清单不算）。每部按类型投票，评分越高权重越大（权重为 0.7 加上 0.1 乘评分，没评分按 3 星算），一部片属于多个配色时平分权重。得分最高的配色胜出。动作等没有对应配色的类型不投票。没有记录或没有类型信息时，用“午夜胶片”，并在左侧说明原因。

如果系统设置了“减少动态效果”，页面会关掉打字机、计数和背景动画。

### Agent 的行为约束

Agent 的行为由[系统提示](../app/agent.py)约束：

- 先读你写下的记录，再用追问的方式聊，一次只问一个问题。
- 需要事实、影评、幕后信息时调用联网搜索，回答里用 `[标题](链接)` 标注来源，搜不到就直说。
- 推荐电影时必须解释“为什么适合你”。
- 你给出一句模糊的灵感，它会给出三个方向（短文、摄影、短视频脚本各一个）让你选。
- 台词必须是你给的原话，不改写。
- 工具返回错误时，把错误码和原因如实告诉你，不假装成功。

一次回复里，Agent 最多连续调用 `MAX_AGENT_STEPS` 轮工具（默认 6）。用完还没有最终答案，会返回 `agent_step_limit`，让你把问题收窄再问。对话历史取最近 40 条。

如果这一轮改动了日记（新增或更新），页面会自动刷新日历。

### 剧照拼贴怎么拼

**选台词：**

- 直接在文本框里写，每行一句。
- 或点「查 Wikiquote 台词」：先用 TMDB 的英文片名查英文版，再用中文片名查中文版，两边都有的话分组列出，每组带来源链接。勾选即可填进文本框。
- 你收藏过的台词会列在最上面。勾选新查到的台词后，可以点「收藏所选」存下来。
- 拼贴最多 4 句，每句不超过 120 字。选满后其余选项变灰。

**怎么拼：**

1. 向 TMDB 取这部片的横版剧照，只留宽度不低于 1280 的，最多 8 张。排序：不带文字的优先，其次评分高，再次分辨率高。
2. 1 到 2 句台词用 3 张剧照，3 到 4 句用 4 张。实际会多下载 2 张备用，下载失败的跳过。
3. 版式（图片区占画布上方 62%，画布 1024×1536）：

   | 张数 | 排法 |
   |---|---|
   | 1 | 一张铺满 |
   | 2 | 上下各一张 |
   | 3 | 上面一张宽图，下面左右各一张 |
   | 4 | 2×2 |

4. 每张剧照按“铺满格子”缩放后居中裁切，不识别主体。
5. 图片区底部叠一层渐变遮罩，台词排在下方黑底上：字号从 50 开始，放不下就依次缩到 22；片名和年份固定在最底部。

拼贴不调用生图模型，不花钱，几秒出图。剧照是这部片的通用画面，和台词出现的那一幕无关。

**限制：**

- 手动添加、没有 TMDB 编号的电影没法拼贴，会提示 `entry_has_no_tmdb_id`。
- 剧照从 `image.tmdb.org` 下载。如果你的网络或代理拦了这个域名，会得到 `still_unreachable`，查台词（走 Wikiquote）不受影响。
- 一张剧照都下载不下来时，直接报错，不会生成半成品。
- Wikiquote 对匿名请求有限流。查询结果缓存 6 小时。被限流时返回 `quotes_rate_limited`。
- 中文版 Wikiquote 覆盖很薄，多数片子只有英文原文。
- 台词内容遵循 Wikiquote 的 CC BY-SA 授权，界面保留了来源链接。

### AI 生图海报

选「AI 生图」，只用 1 句台词。

1. 大模型读这部电影的记录和你给的台词，写出一份 JSON：意境（`mood`）、配色（`palette`）、生图提示词（`image_prompt`）。
2. 把提示词交给生图模型，得到背景图。
3. 台词文字**由程序排版**叠在背景上，不让生图模型画字，所以不会出现错字。
4. 生图失败（超时、网络、鉴权、内容被拒等）时，自动退回用配色生成的渐变背景，海报照样出，并在界面上显示错误码和原因。

生图一张竖版图约 150 秒，等待上限由 `IMAGE_TIMEOUT` 控制（默认 600 秒）。

海报文字需要含中文字形的字体。程序会按顺序找 macOS（冬青黑体、华文黑体、宋体）、Linux（Noto CJK、文泉驿）、Windows（微软雅黑）的系统字体。都找不到会返回 `poster_font_missing`，这时在 `.env` 里设 `POSTER_FONT` 指向一个 `.ttf` 或 `.ttc` 文件即可。

### 收藏的限制

- **台词**：在电影详情点「+ 收藏台词」，或在对话里让 Agent 收藏，或在海报对话框里勾选 Wikiquote 台词后收藏。上限 2000 字，备注上限 200 字。
- **截图**：点「+ 上传截图」。上限 10 MB、5000 万像素。上传后统一转成 JPEG（质量 92），长边最长 2400 像素，只保留画面，不保留原文件的元数据。
- **诗**：从「一键成诗」保存。

### 成诗用到的材料

- 模型拿到的材料：片名、年份、导演、类型、简介（前 300 字）、你的评分、你的感受、标签，以及你收藏过的至多 3 句台词。

### 记录与想看清单的行为

- 搜索后点「看过」，日期取日历上选中的那天；选中的是未来日期时取今天。
- 星星点同一颗不会取消，要改就点别的星。
- 编辑里改了观影日期，保存后日历会跳到新日期。
- 同一部电影不会重复入库（按 TMDB 编号，或片名加年份判断），重复添加返回 `entry_already_exists`。
- 对想看清单里的片子再点「看过」，会直接提升为已看，不报重复。
- 点「看完了 →」转成已看时，日期规则同上，并直接展开编辑框。
- 在对话里说“我想看……”，Agent 会放进清单；说“看完了”，它把状态改回 watched。
- 没有标日期的电影列在日历下方的“N 部没有标日期”里。
- “去年今天”只显示往年同月同日看过的已看条目，没有时不显示。

### 对话范围

- 全局对话和单片对话分开保存。单片对话会把这部电影的完整记录放进上下文。
- 「清空对话」只清当前范围的聊天记录，不动日记。

### 一键成诗的其他规则

- 外国诗人的诗以中文译诗的形态写。
- 同一首诗不会重复保存，重复保存返回 `poem_already_saved`。
