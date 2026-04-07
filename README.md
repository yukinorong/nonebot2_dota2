# QQ 机器人

这是一个运行在 NoneBot2 + OneBot V11 之上的群聊 QQ 机器人，当前用于三个核心场景：

- Dota2 战绩播报、手动查询与本地知识问答
- 联网问答与当日新闻整理
- 带群聊上下文的互动回复与冷场笑话

这份文档是项目的完整说明书。即使脱离当前 Codex 对话，仅通过阅读本文，也应该能够理解项目的功能、运行方式、配置项、主要模块、消息流和维护方式。

## 1. 项目定位

机器人主要服务于固定几个 QQ 群，特点是：

- 群里通过 `@机器人` + 内容触发交互
- 同时支持本地命令和自然语言入口
- 自然语言场景里，机器人能处理功能介绍、Dota2 专业问答、联网问题、群聊互动
- 群内最近 2 小时聊天记录会被用于增强互动回复
- 群冷场太久时，机器人会在限定时段主动发一条非常冷的短笑话

## 2. 运行方式

项目目录：`game_demo/nonebot2`

入口文件：`bot.py`

启动逻辑：

- `nonebot.init()` 初始化运行时
- 注册 OneBot V11 适配器
- 通过 `nonebot.load_plugins("plugins")` 加载 `plugins/` 目录下所有插件
- `nonebot.run()` 启动 WebSocket 客户端

当前服务通过用户级 systemd 运行：

- unit：`nonebot2.service`
- 启动脚本：`start.sh`
- 最终进程：`python -u bot.py`

`start.sh` 会：

- 切换到项目目录
- 激活 `.venv`
- 把日志重定向到 `logs/nonebot.log`

## 3. 环境依赖与配置

最小运行依赖在 `requirements.txt`：

- `nonebot2[websockets]`
- `nonebot-adapter-onebot`
- `redis`

主要配置来自本地 `.env`，仓库内提供示例文件 [.env.example](/home/futunan/data/study_code/game_demo/nonebot2/.env.example) 作为参考模板。

### 3.1 基础通信配置

- `DRIVER=~websockets`
- `HOST=127.0.0.1`
- `PORT=8080`
- `ONEBOT_V11_WS_URLS=["ws://127.0.0.1:6098"]`
- `ONEBOT_V11_ACCESS_TOKEN=...`
- `LOG_LEVEL=INFO`

### 3.2 OpenClaw / LLM 配置

- `OPENCLAW_URL`
- `OPENCLAW_TOKEN`
- `OPENCLAW_MODEL`
- `OPENCLAW_AGENT_ID`
- `OPENCLAW_ROUTER_AGENT_ID`
- `OPENCLAW_ROUTER_MODEL`
- `OPENCLAW_CONFIG_PATH`
  - OpenClaw 网关配置文件路径，群专属 agent 会同步写入这里
- `QQ_GROUP_OPENCLAW_WORKSPACE_ROOT`
  - 每个群的 OpenClaw 长期记忆 workspace 根目录

当前约定：

- 主回答模型通过 `ask_main()` 调用
- 轻量分类或简单互动可以通过 `ask_router()` 调用
- 所有调用通过 `plugins/common.py` 中的 `OpenClawClient` 封装
- `group_chat` 会切到群专属 OpenClaw agent，不再和其他群共享长期记忆空间

### 3.3 QQ 群配置

- `QQ_ALLOWED_GROUP_IDS`
  - 允许机器人接入与记录消息的群
- `QQ_BOT_ACK_EMOJI_ID`
  - 收到 `@机器人` 消息时，先给原消息点一个表情反馈
- `QQ_IDLE_JOKE_GROUP_IDS`
  - 冷场笑话启用群列表，逗号分隔
  - 当前默认值：`1081502166`

### 3.4 项目文档与工作区配置

- `QQ_BOT_WORKSPACE`
- `QQ_BOT_TODOLIST_PATH`
- `QQ_BOT_DESCRIPTION_PATH`

这些配置由 `content_store.py` 读取，用于：

- 读取群简介 `description.md`
- 读取/更新 `todolist.md`

### 3.5 Tavily 联网搜索配置

- `TAVILY_API_KEY`
- `TAVILY_API_URL`
- `TAVILY_MAX_RESULTS`

Tavily 用于：

- 自然语言联网问答
- `/news` 当日新闻整理

### 3.6 Dota2 配置

- `DOTA2_ENABLED`
- `DOTA2_STEAM_API_KEY`
- `DOTA2_STEAM_API_BASE`
- `DOTA2_NOTIFY_GROUP_ID`
- `DOTA2_POLL_INTERVAL_SECONDS`
- `DOTA2_HISTORY_WINDOW`
- `DOTA2_SEQUENCE_BATCH_SIZE`
- `DOTA2_OUTPUT_VERSION`
- `DOTA2_V2_MAX_MATCHES_PER_RUN`
- `DOTA2_V2_STARTUP_BACKFILL_MATCHES`
- `DOTA2_V2_DEBUG`
  - v2 推送调试开关的默认初始值。真实运行态会持久化到 Redis，`/dota_debug on|off|status` 修改的是 Redis 里的 `dota2_v2_debug`，服务重启后不会丢；只有 Redis 里还没有该字段时，才会用 `.env` 的这个值初始化。
  - 开启后会在日志里打印原始比赛数据、昵称/英雄/物品替换后的 payload、LLM 原始返回文本，并带上 match_id 与 group_id
- `DOTA2_HERO_CACHE_FILE`
- `DOTA2_ITEM_CACHE_FILE`
- `DOTA2_MATCH_DB_PATH`
  - Dota2 比赛历史 SQLite 数据库文件路径，默认 `data/dota2_monitor/matches.sqlite3`
  - 当前库中包含两张核心表：`raw_matches` 保存整场原始比赛 JSON，`player_match_analysis` 只保存监听玩家的个人分析数据，并以 `match_id + steam_id` 去重
- `DOTA2_KNOWLEDGE_ENABLED`
  - 是否启用 OpenDota 本地知识同步
- `DOTA2_KNOWLEDGE_SYNC_INTERVAL_SECONDS`
  - OpenDota 知识同步间隔，当前建议 21600 秒
- `DOTA2_KNOWLEDGE_DATA_DIR`
  - 本地 Dota2 知识库存储目录
- `DOTA2_KNOWLEDGE_MATCHUP_MIN_GAMES`
  - 对位统计最小样本数，低于阈值的对位不用于摘要
- `DOTA2_KNOWLEDGE_USE_TAVILY_FOR_PATCH`
  - 问最新补丁时是否允许用 Tavily 补充官方更新信息

### 3.7 Redis 群聊状态存储配置

群聊上下文和冷场状态依赖 Redis，当前是强依赖模式。`group_chat_store` 启动时会初始化 Redis 客户端并执行 `PING`，如果 Redis 不可用会直接报错，不会静默退回进程内存。

- `QQ_GROUP_CHAT_REDIS_URL`
  - Redis 连接 URL，例如 `redis://127.0.0.1:6379/0`
- `QQ_GROUP_CHAT_REDIS_KEY_PREFIX`
  - Redis key 前缀，默认 `nonebot2:group_chat`
- `QQ_GROUP_MEMORY_INTERVAL_SECONDS`
  - 长期记忆整理间隔，默认 7200 秒
- `QQ_GROUP_MEMORY_MAX_CONTEXT_ITEMS`
  - 每轮整理时最多带入多少条最近群聊，默认 100

当前 Redis 里会保存两类状态：

- 群聊上下文和冷场时间
- 可动态修改且需要跨重启保留的运行态开关，例如 `dota2_v2_debug`

本项目只负责连接 Redis，不负责部署 Redis 服务本身。上线前需要先准备好 Redis 实例，并在 `.venv` 安装 `redis` Python 包。

## 4. 功能总览

### 4.1 指令入口

所有命令都要求在群里 `@机器人` 后触发。

支持的本地命令：

- `/help`
  - 返回群简介文档内容
- `/list`
  - 查看当前群正在监听哪些 Dota2 昵称
- `/add <昵称> <steamID>`
  - 给当前群增加一个 Dota2 监听账号
- `/push <昵称>`
  - 手动拉取该昵称最近一场比赛并返回战绩点评
- `/dota_collect [昵称或steamID] [数量]`
  - 不传参数时，会批量采集所有监听账号最近 50 场比赛详情到本地 SQLite
  - 传参数时，采集该账号最近 N 场比赛详情；会先查原始比赛表，已存在的 match_id 直接跳过
  - 这是内部管理命令，只允许 QQ `863347350` 在群 `1081502166` 中使用
- `/dota_rebuild_analysis [昵称或steamID]`
  - 从 `raw_matches` 反填 `player_match_analysis`，只补监听玩家数据
  - 不传参数时扫描全部原始比赛；传参时只补指定账号
  - 这是内部管理命令，只允许 QQ `863347350` 在群 `1081502166` 中使用
- `/dota_analyze <昵称或steamID>`
  - 统计该账号最近 50 场比赛的胜率、最多使用英雄、最高击杀和最高死亡比赛
- `/dota_profile <昵称或steamID>`
  - 基于最近 50 场比赛做深度打法画像、问题总结和改进建议
- `/dota_guide <英雄名>`
  - 生成当前版本的深度英雄攻略，结合本地知识和外部最新资料
- `/news`
  - 整理当天热点新闻
- `/news <关键词>`
  - 整理当天指定主题新闻
- `/todo`
  - 查看当前待办列表
- `/todo <文本>`
  - 提交一个新的功能建议

超级用户命令：

- `/sendqq <qq号> <消息>`
  - 主动发私聊
- `/sendgroup <群号> <消息>`
  - 主动发群消息
- `/dota_check`
  - 触发一次 Dota2 轮询检查
- `/dota_check refresh`
  - 刷新 Dota2 英雄/物品缓存
- `/dota_backfill_v2 [数量]`
  - 补推最近 N 把比赛
- `/dota_knowledge_sync`
  - 手动同步一次本地 Dota2 知识库
- `/dota_debug on|off|status`
  - 动态开启、关闭、查看 Dota2 v2 推送 debug 日志，不需要重启服务
  - 状态保存在 Redis 运行态参数里，重启 `nonebot2.service` 后仍然保留
- `/ping`
  - 健康检查


Dota2 采集与反填日志：

- `collect_recent_matches`、`GetMatchHistory`、`GetMatchHistoryBySequenceNum`、SQLite 落库、`raw_matches -> player_match_analysis` 反填现在都会打印阶段化日志
- 失败日志会尽量携带 `account_id`、`match_id`、`match_seq_num`、`steam_id` 和失败原因
- API 异常或原始数据异常时会直接放弃当前条目，不写入假数据或占位数据

命令型机器人回复不会写入群聊上下文。

### 4.2 自然语言入口

自然语言入口分为四类：

- `version_query`
  - 用户在问“你有什么功能”“你能做什么”
  - 直接返回 `description.md` 内容
- `dota_query`
  - 用户在问 Dota2 英雄、出装、克制、阵容、版本、术语、打法等问题
  - 优先走本地 Dota2 知识库，再按需补 Tavily 的最新补丁信息
  - 最终会带上最近 2 小时群聊上下文，但上下文只辅助语气和承接，不影响专业结论
- `web_answerable`
  - 用户在问明显需要联网的问题，例如天气、新闻、官网信息、实时资料
  - 先通过 Tavily 搜索，再由 LLM 整理回答
- `group_chat`
  - 普通闲聊、接梗、吐槽机器人、批评机器人、骂机器人等都进入这个统一分支
  - 会带上最近 2 小时群聊上下文和该群长期记忆，让回复更像在接群里的上文

其中只有：

- `group_chat`
- `web_answerable`
- `idle_joke`

这三类机器人消息会计入群聊记录。

`version_query`、`dota_query` 和所有命令回复都不会计入上下文。

## 5. 群聊上下文机制

群聊记录由 `plugins/group_chat_store.py` 统一维护，数据存储在 Redis，不再依赖进程内存。

### 5.1 记录范围

记录所有允许群内的群友消息。

机器人只记录三类消息：

- `group_chat`
- `web_answerable`
- `idle_joke`

不记录：

- `/help`、`/todo`、`/news`、`/push` 等命令回复
- 功能简介类 `version_query` 回复
- 群友发送的命令消息文本本身

### 5.2 窗口与裁剪规则

- 每个群单独维护记录
- 只保留最近 2 小时消息
- 提供给 LLM 的上下文最多取最近 100 条
- 服务重启后聊天记录、最近活跃时间、最近冷笑话时间都会从 Redis 继续读取，不会因为重启丢失

### 5.3 消息归一化

对上下文记录的消息内容：

- 优先使用纯文本
- 纯图片、转发、回复等非文本消息会被归一化成占位符，例如 `[图片]`、`[回复]`
- 空消息不会写入上下文文本，但群友事件仍视作活跃行为
- 群友的 `/news`、`/todo` 等命令不会进入上下文，但仍会更新群活跃时间，用于冷笑话判断

### 5.4 对 LLM 的作用

`group_chat` 路由会把最近群聊整理成类似下面的上下文：

```text
13:05 老王: 今晚打不打
13:06 机器人: 你先把人凑齐再说
13:08 小李: 他昨天坑麻了
```

然后要求模型：

- 如果是普通闲聊，就自然接话
- 如果是在阴阳或批评机器人，就接梗并简短回怼
- 不要现实侮辱，不要仇恨，不要长篇输出

### 5.5 群长期记忆

长期记忆已经迁移到 OpenClaw 群专属 workspace 中，NoneBot 不再把 `data/group_memory/<group_id>.memory.md` 当作运行时事实源。

当前链路分三步：

- 后台每 2 小时读取该群最近 2 小时、最多 100 条群聊
- 先让 LLM 提取“结构化长期记忆候选项”，再把候选项和现有记忆项做合并
- 把合并后的结构化记忆项同步到本地 SQLite 检索库，并渲染成该群 workspace 下的 `MEMORY.md`

长期记忆只允许保留三类信息：

- 机器人行为偏好
- 群内稳定词典、固定称呼、别名
- 会影响机器人长期回复的稳定背景

明确不进入长期记忆：

- 群友在群里做了什么
- 一次性闲聊、短期事件、临时计划、短期新闻、个人动态
- 不明确、无法确认、不会影响机器人后续回复的信息

每个群的 OpenClaw 记忆 workspace 默认位于：

- `data/openclaw_group_memory/<group_id>/`

其中会维护：

- `MEMORY.md`
  - 供人工查看的长期记忆视图
- `.memory-items.json`
  - 结构化长期记忆项集合
- `data/group_memory/group_memory.sqlite3`
  - NoneBot 使用的结构化长期记忆检索库

当前 `group_chat` 不再把长期记忆全文直接塞进 prompt，也不再依赖 OpenClaw 的 embedding memory search。NoneBot 会先基于 `.memory-items.json` 和 SQLite 索引做结构化召回，只把命中的 1 到 3 条长期记忆片段注入到群专属 OpenClaw agent prompt 中。
`web_answerable`、`dota_query` 和命令回复仍不注入长期记忆，避免污染联网或专业回答。

## 6. 冷场笑话机制

冷场笑话由 `plugins/idle_joke.py` 提供，是一个后台循环插件。

### 6.1 检查规则

- 启动后常驻运行
- 每 1 分钟检查一次
- 只检查 `QQ_IDLE_JOKE_GROUP_IDS` 指定的群
- 仅在本地时间 `13:00 <= now < 22:00` 时允许发送
- 如果距离该群最近一次活动超过 1 小时，则发一条冷笑话
- 默认版本为 `v2`，走外部笑话接口，不再默认用 LLM 现场生成

### 6.2 去重规则

每个群会在 Redis 里维护：

- 消息 ZSET：`<prefix>:group:<group_id>:messages`
- 状态 HASH：`<prefix>:group:<group_id>:status`
- 群索引 SET：`<prefix>:groups`

其中状态 HASH 包含：

- `last_activity_at`
- `last_idle_joke_at`

当且仅当：

- 已经超过 1 小时无人活动
- 且上一次冷笑话不是在当前这轮冷场里发的

才会再次发送。

只要出现新消息，活跃时间就会更新，下一轮冷场才可能重新发。

此外，`v2` 冷笑话还会写入独立 SQLite 去重库：

- 数据库文件：`data/idle_joke/jokes.sqlite3`
- 表：`idle_joke_hashes`
- 去重范围：**按群**
- 规则：对最终准备发送的笑话文本做标准化后计算 MD5，同一群里命中相同哈希就继续请求下一条，不会重复推送

如果连续请求都命中重复或接口失败，本轮就直接跳过，不发占位消息。

### 6.3 冷笑话 V2 接口

默认接口：

```text
https://tools.mgtv100.com/external/v1/pear/duanZi
```

当接口返回成功时：

- 读取 `data` 字段
- 把 `<br>` 替换成换行
- 做 QQ 文本清洗
- 按群做 MD5 去重
- 通过后再推送到群里

如果接口失败、返回结构异常、或连续命中重复，则本轮放弃，不发假数据。

### 6.4 冷笑话 V1 备用逻辑

保留原来的 LLM 生成链路作为 `v1` 备用版本，固定 prompt 为：

```text
讲一个冷笑话，要非常冷，听了之后尴尬得想笑。不要黄暴内容，不要重复老梗，控制在50字以内，只回复笑话正文，不加任何解释或表情
```

主动冷笑话会计入聊天记录，并且视作恢复活跃。

## 7. Dota2 本地知识库与 `dota_query`

Dota2 专业问答在现有战绩播报之外，新增了一层本地知识库。

### 7.1 为什么首版不使用向量 RAG

当前问题主要围绕：

- 英雄怎么玩
- 常见出装
- 克制关系
- 胜率与版本强势
- 最新补丁方向

这些知识天然是结构化数据，更适合用：

- 英雄 / 装备别名词典匹配
- 规则化意图识别
- 本地 JSON 缓存召回
- LLM 负责把结构化结果组织成自然语言

因此首版采用的是轻量检索，而不是 embedding + 向量库 RAG。只有未来接入大量攻略文章、复盘长文、群聊沉淀文本时，才有必要升级成混合检索。

### 7.2 本地知识目录

目录：`data/dota_knowledge/`

主要文件：

- `hero_stats.json`
  - OpenDota `heroStats` 原始缓存
- `hero_matchups.json`
  - 每个英雄的对位数据
- `hero_item_popularity.json`
  - 每个英雄各阶段热门出装
- `hero_durations.json`
  - 每个英雄不同时长表现
- `hero_aliases.json`
  - 英雄别名字典
- `item_aliases.json`
  - 装备别名字典
- `derived/hero_briefs.json`
  - 机器人直接读取的英雄摘要
- `derived/meta_briefs.json`
  - 高胜率英雄和版本趋势摘要
- `meta.json`
  - 整体同步时间与规模信息
- `sync_state.json`
  - 最近一次同步是否成功、错误摘要等

### 7.3 OpenDota 同步内容

`plugins/dota_knowledge_sync.py` 负责后台同步 OpenDota。

当前会抓：

- `/heroStats`
- `/heroes/{id}/matchups`
- `/heroes/{id}/itemPopularity`
- `/heroes/{id}/durations`

同步后会派生出：

- 英雄角色和基础定位
- 公共对局综合胜率
- 强势期标签
- 常见热门装备
- 对位摘要
- 当前高胜率英雄列表

后台策略：

- 插件启动后常驻运行
- 每 `DOTA2_KNOWLEDGE_SYNC_INTERVAL_SECONDS` 秒同步一次
- 同步失败不会阻塞机器人主流程
- 本地没有缓存时，`dota_query` 会明确提示知识库尚未准备好

### 7.4 匹配准确度策略

`plugins/dota_query.py` 先做本地解析，再做生成。

解析顺序：

1. 文本归一化
2. 英雄 / 装备别名字典匹配
3. 规则化意图判断
4. 低置信度时短澄清，而不是硬答

这样做的目的，是让“蓝猫”“白牛”“跳刀”“BKB”这类群聊黑话优先在本地被识别，不完全依赖模型猜实体。

### 7.5 `dota_query` 的回答链路

1. 检测消息是否明显是 Dota2 问题
2. 识别英雄、装备、意图
3. 从 `data/dota_knowledge/` 召回结构化摘要
4. 如果是在问最新补丁，额外用 Tavily 补充官方更新信息
5. 一并带上最近 2 小时、最多 100 条群聊上下文
6. 发给 LLM 组织成 3 到 6 句中文回答

注意：

- 群聊上下文只用于辅助语气、接梗、承接群内话题
- 专业结论必须以知识库和检索结果为准
- `dota_query` 的机器人回复不会写回群聊记录，避免污染闲聊上下文

## 8. Tavily 联网问答与新闻整理

`qq_router.py` 里维护了 Tavily 直连逻辑：

- `_tavily_search_sync()`
- `_tavily_search()`
- `_build_tavily_context()`

`web_answerable` 的处理流程：

1. 把用户问题直接交给 Tavily
2. 将 Tavily 返回的标题、摘要、链接拼成上下文
3. 交给 LLM 做 1 到 3 句中文结论整理

### 10.6 Dota2 比赛历史数据库

Dota2 比赛历史数据使用 SQLite，路径由 `DOTA2_MATCH_DB_PATH` 控制。

当前表结构：

- `raw_matches`
  - 按 `match_id` 保存 `GetMatchHistoryBySequenceNum` 返回的整场原始 JSON
  - 采集接口在调用 by-seq 前会先查这张表；如果该 `match_id` 已存在，就直接跳过，不再重复请求详情
- `player_match_analysis`
  - 只保存监听玩家的个人分析数据，不保存整场 10 名玩家
  - 以 `match_id + steam_id` 作为去重标准；命中已存在记录时直接跳过，不更新旧行
  - 保存英雄、KDA、经济、伤害、装备栏、技能加点 JSON 等分析字段
- `data/dota_knowledge/guide_sources.sqlite3`
  - 保存英雄攻略的外部来源素材
  - 会记录来源、抓取时间、适用版本、过期时间，并按内容哈希去重
  - 超过三个月的数据会淘汰；同大版本旧小版本会略微降权，上一个大版本会明显降权

数据进入数据库的方式有两类：

- 自动/手动获取比赛详情时，拿到 by-seq 详情后会写入原始表，并只为监听玩家写分析表
- `/dota_collect` 不带参数时会遍历所有监听账号并各采最近 50 场
- `/dota_collect <昵称或steamID> [数量]` 会先调用 `GetMatchHistory` 找最近 N 场，再逐场按 `match_id` 判重后补采详情
- `/dota_analyze <昵称或steamID>` 直接读取 `player_match_analysis`，统计最近 50 场的固定指标，不经过大模型
- `/dota_profile <昵称或steamID>` 会先做结构化特征提取，再交给 LLM 做深度复盘
- `/dota_guide <英雄名>` 会优先读本地知识，再从攻略来源库中召回当前版本素材，必要时补抓外部最新资料

`/news` 的处理流程：

1. 用“当天日期 + 新闻热点”构造查询
2. 可选追加关键词
3. 由 LLM 整理为新闻列表
4. 再做文本后处理，保证输出在 QQ 里有换行

`/news` 输出格式目标：

```text
今日新闻
1. 标题：摘要
2. 标题：摘要
来源：https://...
```

## 9. Dota2 功能说明

Dota2 相关代码主要在：

- `plugins/dota2_service.py`
- `plugins/dota2_monitor.py`
- `plugins/dota2_watch_config.py`

能力包括：

- 定时轮询 Steam API
- 检测是否有新比赛
- 自动推送到配置群
- 如果多个监听账号在同一局比赛里，会按 match_id 和群号合并成一次推送，避免同一局重复刷屏
- 支持手动查询最近一场比赛
- 维护英雄、物品缓存
- 通过 OpenClaw 生成群聊风格点评

配置文件：

- `data/dota2_monitor/watch_config.json`
  - 本地运行时配置，不再纳入版本库
- `data/dota2_monitor/watch_config.example.json`
  - 可提交的示例模板
  - 维护昵称与群号映射
- `data/dota2_monitor/heroes.json`
- `data/dota2_monitor/items.json`
- `data/dota2_monitor/state.json`

当前 `watch_config.json` 的结构：

- `nicknames`
  - 昵称到 SteamID 的映射
- `group_map`
  - SteamID 到群号列表的映射

## 10. 主要插件与职责

`plugins/` 目录下的核心插件如下：

- `qq_entry.py`
  - 群消息记录、`@机器人` 主入口、命令优先、自然语言分发
- `qq_commands.py`
  - 本地命令解析与处理
- `qq_router.py`
  - 自然语言分类、Dota2 问答分流、联网问答、群聊互动、新闻整理
- `dota_query.py`
  - Dota2 问题识别、实体匹配、知识召回、回答组织
- `dota_knowledge_store.py`
  - 本地 Dota2 知识文件读写、别名字典、派生摘要加载
- `dota_knowledge_sync.py`
  - OpenDota 本地知识库后台同步
- `group_chat_store.py`
  - 通用群聊上下文与活跃状态模块
- `idle_joke.py`
  - 冷场笑话后台任务
- `dota2_monitor.py`
  - Dota2 后台轮询任务
- `dota2_service.py`
  - Dota2 数据拉取、缓存、文本生成
- `dota2_watch_config.py`
  - Dota2 昵称和群映射配置读写
- `content_store.py`
  - `README`/`description`/`todolist` 相关内容读取与待办写入
- `llm_gateway.py`
  - OpenClaw 调用入口
- `common.py`
  - 通用 HTTP、发送 QQ 消息、环境变量读取、OpenClaw 客户端封装
- `ping.py`
  - 简单的 `/ping`

## 11. 典型消息流

### 11.1 群友普通发言

1. 所有允许群的消息先进入记录器
2. 群友消息写入群聊记录模块
3. 如果没有 `@机器人`，流程结束

### 11.2 群友 `@机器人 /news`

1. 群友消息先写入记录
2. 命中 `qq_entry` 的 `to_me()` 入口
3. 先加 ack 表情
4. 命中 `qq_commands.py` 的本地命令路径
5. 调 Tavily + LLM 整理新闻
6. 回复消息发送到群里
7. 因为这是命令型回复，所以**不写入聊天上下文**

### 11.3 群友 `@机器人 你今天怎么这么笨`

1. 群友消息写入记录
2. 分类到 `group_chat`
3. 读取最近 2 小时、最多 100 条群聊上下文
4. 发给 LLM，让它结合语境接梗/回怼
5. 机器人回复发送后写回记录模块

### 11.4 群友问 Dota2 问题

1. 入口先识别为 `dota_query`
2. 本地匹配英雄、装备和意图
3. 从 `data/dota_knowledge/` 召回结构化摘要
4. 如果在问最新补丁，再补 Tavily 的官方更新结果
5. 带上最近 2 小时群聊上下文，但只作为辅助
6. LLM 输出偏专业、但带一点群聊口吻的回答

### 11.5 群里长时间没人说话

1. `idle_joke.py` 每 1 分钟跑一次检查
2. 如果群在允许时段内且超过 1 小时无活动
3. LLM 生成一条 50 字内冷笑话
4. 主动发送到群里
5. 这条消息会写回聊天记录，并重置活跃状态

## 12. 文档与待办约定

项目维护三份文档：

- `README.md`
  - 详细文档，面向开发与维护
- `description.md`
  - 群简介，只暴露功能、接口和少量说明
- `todolist.md`
  - 待办列表，由群友和开发者共同维护

`todolist.md` 的维护规则：

- 新功能完成后，需要检查是否有对应待办
- 如果有，则标记为已完成
- 默认不删除已有 todo，除非用户特别要求

## 13. 开发与验证建议

本地快速检查：

- `python -m py_compile plugins/*.py tests/*.py`
- 使用项目虚拟环境执行测试：`.venv/bin/python -m unittest ...`
- 改完后重启用户级服务：`systemctl --user restart nonebot2.service`
- 看运行日志：`logs/nonebot.log`

重点关注：

- 命令回复是否误写入群聊上下文
- `group_chat` 是否确实带上了上下文
- `dota_query` 是否优先命中本地知识，而不是误走普通联网问答
- `dota_query` prompt 是否明确了“群聊上下文只辅助，不影响专业结论”
- 冷笑话是否只在 13:00 到 21:59 之间触发
- 冷笑话是否只对 `QQ_IDLE_JOKE_GROUP_IDS` 中的群生效
- `/news` 输出是否仍保持多行格式
