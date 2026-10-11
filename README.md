# KB Agent

基于 **DeepAgents SDK** 的本地知识库智能体系统。产品流程参考 [PandaWiki](https://github.com/chaitin/PandaWiki) 的知识库、文档管理和问答体验，独立实现；没有复制其代码或素材。

当前版本面向**本地单用户开发**，无需模型密钥即可运行。未配置模型时提供原文检索，配置模型后启用真正的 DeepAgents 工具调用。

## 已实现

- 中文 Web 工作台，实时文档、片段和字符统计。
- 多知识库创建、切换和删除；文档与会话按知识库隔离。
- Markdown 新建、编辑、基础安全预览和导出。
- Obsidian 风格双向链接、编辑自动补全、反向引用侧栏和交互式局部图谱。
- Markdown / TXT / PDF / DOCX 导入，单文件默认最大 10 MB，单文档最大 200,000 字符。
- 中文分词 + MySQL 词项索引 / BM25，无需嵌入模型，文档更新和删除时事务性维护索引。
- DeepAgents 主智能体、知识检索子智能体、规划、检索/阅读/反向引用/关联查询工具，以及虚拟文件工作区。
- 问答过程通过 SSE 显示工具执行进度，完成后返回答案和可点击的文档引用。
- MySQL 持久化文档、关联、检索索引、会话和已完成的问答；MinIO 保存上传原件，支持下载；失败请求不会保存半个对话轮次。
- DeepWiki 风格代码 Wiki：自建 GitLab / 已 clone 本地目录、按分支同步、结构 Wiki、分层 AI 文件/模块/架构文档、失败续跑、增量分析、带提交号与行号的源码引用。
- 不配置模型时，返回明确标注的原文片段，不假冒 AI 答案。

## 本地启动

要求 Git、Python **3.12 或 3.13**、[uv](https://docs.astral.sh/uv/getting-started/installation/)，以及 Docker / Docker Compose（或已有的 MySQL 5.7+ 和 MinIO 服务）。Node 仅用于 JavaScript 语法检查。

```bash
bash scripts/setup.sh
make services
bash scripts/start.sh
```

`make services` 首次创建忽略提交的 `.env`，生成随机本地密码，启动 MySQL 8.4 与 MinIO；已有 `.env` 会保留。MinIO 社区公开镜像不可用，因此本地 Compose 使用固定版本的 Bitnami 归档镜像，并锁定 SHA-256 digest。镜像支持 amd64 和 arm64。已有外部服务时跳过 `make services`，复制 `.env.example` 并填写连接信息。

浏览器访问 **http://localhost:8000**，默认仅监听 `127.0.0.1`。健康检查 `GET /api/health` 验证 MySQL 与 MinIO；API 文档在 `/docs`。首次启动为空，可点击「加载示例知识库」创建示例。更换端口可在 `.env` 设置 `KBAGENT_PORT=8010`，或通过环境变量覆盖；避免浏览器禁止的 `6000` 端口。

### 存储配置

在项目根目录 `.env` 配置，修改后重启应用。完整模板见 [.env.example](.env.example)。

```dotenv
KBAGENT_MYSQL_HOST=127.0.0.1
KBAGENT_MYSQL_PORT=3306
KBAGENT_MYSQL_DATABASE=kbagent
KBAGENT_MYSQL_USER=kbagent
KBAGENT_MYSQL_PASSWORD=填写数据库密码

KBAGENT_MINIO_ENDPOINT=127.0.0.1:9000
KBAGENT_MINIO_ACCESS_KEY=填写访问密钥
KBAGENT_MINIO_SECRET_KEY=填写私有密钥
KBAGENT_MINIO_BUCKET=kbagent
KBAGENT_MINIO_SECURE=false
```

MinIO endpoint 使用 `主机:端口`，不包含 `http://` 或路径。HTTPS 服务设置 `KBAGENT_MINIO_SECURE=true`；证书会正常验证。MySQL TLS 可通过 `KBAGENT_MYSQL_SSL_CA` 指定 CA 文件，并验证主机名。`KBAGENT_MINIO_REGION` 默认 `us-east-1`；默认自动创建 bucket，可用 `KBAGENT_MINIO_CREATE_BUCKET=false` 改为只使用已有 bucket。已有 MySQL 5.7 可直接使用，无需升级到 8.0；本地 Compose 默认仍为 8.4。MySQL 数据库必须预先存在，账号需有建表与数据读写权限；本地 Compose 自动初始化。

本地 MinIO 控制台为 `http://localhost:9001`，使用 `.env` 中的访问密钥登录。`MYSQL_ROOT_PASSWORD` 仅用于 Compose 初始化和独立测试库，不用于应用连接。首次初始化后修改 `.env` 中的数据库密码不会自动修改已有 MySQL 用户，请先在服务中更新对应账号。MySQL 和 MinIO 使用 Docker 命名卷，停止容器保留数据；备份需同时保留数据库和 bucket，并使两份备份保持一致。

### 迁移旧 SQLite 数据

停止旧版本服务，配置 MySQL 后执行：

```bash
.venv/bin/python scripts/migrate_sqlite.py --source data/kbagent.sqlite3
```

迁移要求目标数据库为空，保留文档 ID、别名、已绑定链接和会话，重新建立检索索引；失败会回滚数据写入，旧 SQLite 文件保持不变。旧版本只保存提取后的正文，没有上传原件字节，因此历史原件无法恢复。运行后再启动新服务。

`uv.lock` 固定依赖，`scripts/setup.sh` 使用冻结锁文件安装。云环境默认缓存目录为 `/workspace/.cache/uv`；其他机器可以覆盖：`UV_CACHE_DIR="$HOME/.cache/uv" bash scripts/setup.sh`。

## 稍后接入模型

在已有 `.env` 中填写模型配置；不要覆盖存储连接信息或提交 `.env`。

```dotenv
KBAGENT_API_KEY=你的密钥
KBAGENT_MODEL=你的模型名称
KBAGENT_BASE_URL=https://api.openai.com/v1
```

也可以使用支持 OpenAI Chat Completions 和工具调用的兼容模型服务。以 DeepSeek 为例，接口地址可设置为 `https://api.deepseek.com`，模型名称需以服务商实际可用且支持工具调用的型号为准。配置后重启服务，再发起一次问答验证。没有配置模型时，无需这些变量。

密钥不返回给浏览器。模型调用失败时显示错误，不静默降级为检索。默认模型单次请求超时为 45 秒（`KBAGENT_MODEL_TIMEOUT` 可配置），问答任务默认最多 120 秒（`KBAGENT_AGENT_TIMEOUT`）/ 40 个图步骤，最多四个并发问答，同一会话不允许并发发送。云环境接入额外模型服务时，还需在环境设置中允许其域名并安全绑定凭据。

## 双向链接与知识图谱

在 Markdown 文档中输入 `[[`，使用方向键 / Enter / Tab 或鼠标选择补全文档；Escape 关闭建议。支持以下写法：

```markdown
参考 [[部署指南]]。
参考 [[部署指南|操作手册]]。
跳转到 [[部署指南#安装|安装步骤]]。
跳转到 [[#本页标题]]。
```

阅读页的链接可直接打开目标文档；右侧「反向引用」显示引用该页的文档和原文片段。「局部知识图谱」展示一层或两层邻居，可拖动、缩放、筛选节点名称，单击选择节点并打开文档，双击直接打开。箭头表示引用方向；文档列表提供键盘可访问的替代入口。最多显示 80 个节点 / 500 条边，每个方向的引用列表最多 200 条。

文档可以设置逗号分隔的别名；重命名自动保留旧标题作为别名，已建立的链接使用稳定的文档 ID。不存在的文档会显示为待创建链接，点击即可创建并关联。同名文档不会被任意选择，需要明确绑定；自动补全选择同名文档时使用 `[[id:文档ID|显示名称]]`。原目标删除后显示失效，即便新建同名文档也不会自动替换，需要手动修复。修改原文但保留相同的链接文字时保留绑定；删除链接后重新插入会重新解析名称。

链接仅在当前知识库内解析。代码块、行内代码、HTML 注释、缩进代码、转义的链接以及 `![[嵌入]]` 不建立关联。名称、锚点或显示文字超过 200 字符的标记作为普通文本保留。标题锚点目前支持标题文本，不支持 Obsidian 块引用、附件嵌入或完整 Vault 同步。

第一次升级启动会自动为既有文档补建关联索引，不修改原文。文档保存同时更新全文检索与链接索引；关联数据保存在同一 MySQL 数据库中。图谱通过本地打包的 Cytoscape.js 渲染，无需 CDN 或模型服务。

智能体可以调用 `get_backlinks` / `get_related_documents` 沿链接寻找上下文。关联列表不是内容证据，智能体仍需阅读原文才能给出相关文档引用。

## 代码仓库 Wiki

在 `.env` 配置并重启，再进入「代码 Wiki」添加仓库：

```dotenv
# 内网 / 自建 GitLab 的 HTTP(S) 地址，支持根路径，例如 https://gitlab.example.com
KBAGENT_GITLAB_URL=https://gitlab.example.com
KBAGENT_GITLAB_USERNAME=oauth2
KBAGENT_GITLAB_TOKEN=具有read_repository权限的只读Token
# 自签证书使用受信 CA；不关闭 TLS 校验
# KBAGENT_GITLAB_SSL_CA=/path/to/gitlab-ca.pem

# 服务进程所在机器上的允许目录，用 JSON 数组配置
KBAGENT_REPOSITORY_LOCAL_ROOTS=["/srv/repos","/Users/me/projects"]
```

GitLab 来源填写 `https://gitlab.example.com/team/service.git`，支持多层 subgroup。只能连接配置的 GitLab 地址；Token 通过临时 Git askpass 的子进程环境提供，不写入数据库、仓库 URL 或命令参数。项目 Token 如要求专用用户名，可修改 `KBAGENT_GITLAB_USERNAME`。当前支持 HTTP(S) Git 拉取；不使用 GitLab API，不支持 SSH 地址。

本地来源填写已经 clone 的仓库根目录，如 `/srv/repos/service`。这是**服务器文件系统上的路径**；浏览器所在电脑的路径仅在服务也运行于该电脑时有效。允许范围外的目录与子目录入口会被拒绝；符号链接会先解析真实路径。分支留空时使用默认 HEAD，填入分支名时导入该分支已提交的内容。未提交修改、不受 Git 跟踪的文件和子模块不会进入索引。

添加后后台同步，页面显示进度、错误、提交号、导入和跳过的文件数。「同步」重新读取配置的分支。同步成功后自动生成仓库概览、按顶层目录划分的模块页，以及 Python 类/函数行号索引；页面明确标注为**结构 Wiki，非 AI 分析**。源码和生成的 Wiki 由仓库任务管理，不能当普通文档编辑或删除；修改代码并提交后重新同步。

点击「打开 Wiki」可阅读 Wiki 页面、筛选文件和按行号查看源码。普通问答也可检索这些代码；DeepAgents 增加 `search_code`、`list_repository_files`、`read_code` 工具。检索支持中文与 camelCase / snake_case 拆词。代码引用保留仓库、提交号、快照、路径与行号，点击后读取对应版本；新同步不会覆盖旧源码快照。删除仓库或知识库会删除其历史快照，因此引用随后失效。

配置模型后点击「生成 AI Wiki」，通过真正的 DeepAgents 图分层处理**所有已导入的文本文件**：

1. 长文件按字符预算切段，逐段生成职责、接口、依赖、关键条件、异常和测试说明。每个分段都必须有来自服务器实际提供源码的有效引用；空文件和空白段直接标注，不伪造 AI 分析。
2. 保存每份文件文档，按文件所在目录生成模块 Wiki。大文件、大模块通过有界汇总树合并，所有分段/子项均进入汇总流程，不截掉列表尾部。
3. 汇总模块结果为「AI 架构与关键流程」，生成可点击的模块和文件目录，以及「AI 静态依赖关系」索引。静态依赖目前支持 Python 导入与 JS/TS 相对字面量导入候选，不等于完整跨语言或运行时调用图；还可在阅读器的局部知识图谱中沿目录和源码链接浏览。

页面显示文件/分段/模块进度、当前处理路径及复用的检查点数。失败后点击「继续生成 AI Wiki」，或服务重启后重新发起任务，会复用已完成单元，从未完成部分继续；正在生成的单个图不恢复内部状态。检查点保存到 MySQL 的 `repository_wiki_cache`，进度保存到 `repository_wiki_progress`；最终 Wiki 保存为 MySQL 正常文档，可检索、查看关联和导出 Markdown。MinIO 保存源码快照，不将 Wiki 写回本地 Git 目录。

成功时全部 AI 页面及关联在同一事务中发布；失败保留当前快照已有的完整 Wiki。同步新提交后移除旧 AI 页面，避免混用代码版本；检查点仍保留，重新生成会按源码分段内容、文档 ID、模型服务/型号、提示词版本与预算判断复用，未变分段无需重新调用模型，源码或子单元检查点发生变化，会使相应文件、模块和仓库汇总失效，即使两次模型摘要文字相同也重新汇总受影响部分。成功发布后清理不再使用的缓存。无需执行仓库代码或构建。引用校验确认引用来自提供的源码/已校验摘要，不证明模型每项结论都正确，仍须通过源码核对。

“完整”指覆盖此次同步后**已导入范围**的文件，过滤文件、未导入子模块和工作区未提交修改不在其中，也不保证每个符号或调用关系都被模型理解。实际模型服务与用户 GitLab 尚需用户配置；测试使用本地认证 Git HTTP 服务和确定性模型，未连接你的内网仓库或真实模型。

默认单文件最多 200,000 字节、500 个文件、文本总量 10 MB；可在 `.env` 修改 `KBAGENT_REPOSITORY_MAX_FILE_BYTES`、`KBAGENT_REPOSITORY_MAX_FILES` 和 `KBAGENT_REPOSITORY_MAX_TOTAL_BYTES`。跳过常见依赖/生成目录、锁文件、二进制、无效 UTF-8、符号链接、`.env*`、私钥文件与私钥 PEM 内容。这是有限的文件过滤，不是通用秘密扫描；代码中硬编码的密码仍可能进入索引并在启用模型时发送给模型服务。

完整 AI Wiki 的预算按**单元**而非整仓文件数量计算：

| `.env` 配置 | 默认值 | 最大值 | 用途 |
|---|---:|---:|---|
| `KBAGENT_MODEL_TIMEOUT` | 45 | 3600 | 模型单次请求超时，秒 |
| `KBAGENT_AGENT_TIMEOUT` | 120 | 3600 | 每次问答或 Wiki 分析/汇总单元超时，秒 |
| `KBAGENT_WIKI_TASK_TIMEOUT` | 7200 | 86400 | 整仓 AI Wiki 分析时限，秒；发布事务随后完成 |
| `KBAGENT_WIKI_MAX_STEPS` | 80 | 256 | 每个单元的图步骤预算 |
| `KBAGENT_WIKI_SOURCE_CHARS` | 24000 | 100000 | 每个单元的资料字符预算；实际源码段预留 1200 字符给路径与元数据 |
| `KBAGENT_WIKI_BATCH_FILES` | 8 | 100 | 每次汇总最多合并的子项数；同时受字符预算限制 |
| `KBAGENT_WIKI_SUMMARY_CHARS` | 2000 | 12000 | 摘要目标篇幅，目标不超过资料预算的 1/4；允许在安全硬上限内输出更长正文 |

`KBAGENT_WIKI_MAX_FILES` 仅供保留的旧概览函数使用，不再限制「生成 AI Wiki」的全仓覆盖。单元研究最多使用 `KBAGENT_AGENT_TIMEOUT` 的 65%；步骤或研究时间耗尽时，使用已提供资料发起一次无工具整理。摘要目标默认 2,000 字符，不是硬截断。实际输出硬上限为 `min(12000, (KBAGENT_WIKI_SOURCE_CHARS - 1400) // 2)`，默认 11,300 字符，预留两个子摘要与路径标题的合并空间。目标篇幅以上但硬上限以内的合法正文完整保存、参与汇总并可复用，不截断结尾。超硬上限且引用合法、正文仍能装入单次输入时，会把已生成正文提供给模型压缩一次，保留关键实现与源码引用；正文过大或引用非法时使用有界原资料修正一次。仍失败时保存已完成检查点，显示实际长度、硬上限与相关配置，不发布不完整结果。增加超时或图步骤不能解决篇幅超限。分段提供的是字符数，不是 token 数；上下文预算需与模型及 SDK 提示词开销匹配。

较慢的本地模型可以先配置：

```dotenv
KBAGENT_MODEL_TIMEOUT=180
KBAGENT_AGENT_TIMEOUT=600
KBAGENT_WIKI_TASK_TIMEOUT=14400
KBAGENT_WIKI_SOURCE_CHARS=16000
KBAGENT_WIKI_BATCH_FILES=8
KBAGENT_WIKI_SUMMARY_CHARS=2000
```

修改后重启；源码越多，首次分析请求越多，需要更多时间。相同源码和配置再次生成通常直接复用检查点；更换模型服务/型号或摘要预算会重新分析。点击「重新分析全部」可忽略已有缓存，重新调用模型，成功后更新已发布文档；也可调用 `POST /api/spaces/{space_id}/repositories/{id}/wiki?force=true`。

Git 同步仍采用浅克隆、全量刷新索引和手动触发，没有自动 GitLab webhook 或定时拉取。浅克隆的数据传输大小不受导入文本上限约束；大仓库优先使用已有本地 clone。Git 单次命令超时默认 120 秒；服务进程需可访问内网 GitLab。任务状态、进度和检查点保存在 MySQL，服务重启后中断任务会标为失败，可手动续跑，等待中的任务会继续。同步错误与 AI Wiki 错误分别显示；AI 生成失败不会清空代码索引。模型认证、网络、请求超时、上下文超限、限流以及存储错误会分类提示。服务端日志记录任务类型、异常类型和调用位置，不输出异常正文、模型响应或密钥。当前使用单服务进程的串行仓库 worker，请不要启动多实例共享同一数据库处理任务。

## 验证

```bash
# 为本地测试准备独立 MySQL 数据库（需 Docker）
make test-services
make test
make lint
# 可选：已安装 Chromium 时，执行真实浏览器端到端检查
make browser
```

`KBAGENT_TEST_MYSQL_DATABASE` 默认 `kbagent_test`，必须与应用数据库不同且以 `_test` 结尾。测试会清空该测试数据库，并创建、删除独立的临时 bucket；外部服务用户需自行创建测试库并授权。

测试使用真实 MySQL 和 MinIO，覆盖原件上传/下载/删除、清理重试、写入失败回滚、SQLite 迁移，以及 CRUD、中文检索、索引更新/清理、知识库隔离、四种文件格式、大小/格式错误、SSE、会话持久化、重启、链接解析/迁移/重命名/删除修复/同名选择、图谱遍历、真实 DeepAgents 图的工具调用、引用校验和服务端文件隔离。代码仓库测试覆盖真实本地 Git、HTTP Basic 认证拉取、分支选择、文件过滤、事务回滚、历史快照、源码引用、只读保护，以及真实 SDK 的全文件分段分析、汇总树、失败/超时续跑、模型预算、增量缓存失效与静态导入关联。浏览器检查还覆盖仓库添加/同步、完整 Wiki 进度、模块/文件导航、Markdown 导出、源码行号、问答源码引用，以及长回答滚动与输入框可见性、实时/历史回答 Markdown、安全渲染、桌面短窗口和手机布局，以及自动补全、标题跳转、引用侧栏、实际图谱节点点击、关联创建、两层图谱和手机布局。

SDK 测试使用确定性模型测试替身驱动**真实 DeepAgents 图**，无需外部密钥；这不代表真实模型服务已连通。测试与浏览器脚本使用独立的 `_test` 数据库和临时 MinIO bucket，结束后清理测试数据；请勿并行运行这两项。

## 架构与边界

详见 [架构说明](docs/architecture.md)。这是可运行的 MVP，尚未包含团队身份认证、RBAC、公开 Wiki 发布、向量检索、OCR、URL / Sitemap / RSS 导入、AI 辅助创作或第三方聊天平台集成。请保持本机监听，不直接作为多人公网服务部署。

基础预览支持标题、段落、列表行、引用、加粗、行内代码与代码块，不执行原始 HTML，也不提供完整富文本编辑。PDF 暂不支持扫描件 OCR 或加密文件。SSE 当前传送运行进度和最终答案，不逐 token 推送。持久化的是对话消息与引用；每轮智能体的计划、临时文件和子智能体状态不跨请求恢复。

参考：[DeepAgents SDK](https://github.com/langchain-ai/deepagents)、[官方文档](https://docs.langchain.com/oss/python/deepagents/overview)、[PandaWiki](https://github.com/chaitin/PandaWiki)。
