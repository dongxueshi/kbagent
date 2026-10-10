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
- 不配置模型时，返回明确标注的原文片段，不假冒 AI 答案。

## 本地启动

要求 Python **3.12 或 3.13**、[uv](https://docs.astral.sh/uv/getting-started/installation/)，以及 Docker / Docker Compose（或已有的 MySQL 8 和 MinIO 服务）。Node 仅用于 JavaScript 语法检查。

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

MinIO endpoint 使用 `主机:端口`，不包含 `http://` 或路径。HTTPS 服务设置 `KBAGENT_MINIO_SECURE=true`；证书会正常验证。MySQL TLS 可通过 `KBAGENT_MYSQL_SSL_CA` 指定 CA 文件，并验证主机名。`KBAGENT_MINIO_REGION` 默认 `us-east-1`；默认自动创建 bucket，可用 `KBAGENT_MINIO_CREATE_BUCKET=false` 改为只使用已有 bucket。MySQL 数据库必须预先存在，账号需有建表与数据读写权限；本地 Compose 自动初始化。

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

密钥不返回给浏览器。模型调用失败时显示错误，不静默降级为检索。默认请求超时为 45 秒，完整智能体任务最多 120 秒 / 40 个图步骤，最多四个并发问答，同一会话不允许并发发送。云环境接入额外模型服务时，还需在环境设置中允许其域名并安全绑定凭据。

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

测试使用真实 MySQL 和 MinIO，覆盖原件上传/下载/删除、清理重试、写入失败回滚、SQLite 迁移，以及 CRUD、中文检索、索引更新/清理、知识库隔离、四种文件格式、大小/格式错误、SSE、会话持久化、重启、链接解析/迁移/重命名/删除修复/同名选择、图谱遍历、真实 DeepAgents 图的工具调用、引用校验和服务端文件隔离。浏览器检查还覆盖自动补全、标题跳转、引用侧栏、实际图谱节点点击、关联创建、两层图谱和手机布局。

SDK 测试使用确定性模型测试替身驱动**真实 DeepAgents 图**，无需外部密钥；这不代表真实模型服务已连通。测试与浏览器脚本使用独立的 `_test` 数据库和临时 MinIO bucket，结束后清理测试数据；请勿并行运行这两项。

## 架构与边界

详见 [架构说明](docs/architecture.md)。这是可运行的 MVP，尚未包含团队身份认证、RBAC、公开 Wiki 发布、向量检索、OCR、URL / Sitemap / RSS 导入、AI 辅助创作或第三方聊天平台集成。请保持本机监听，不直接作为多人公网服务部署。

基础预览支持标题、段落、列表行、引用与代码块，不执行原始 HTML，也不提供完整富文本编辑。PDF 暂不支持扫描件 OCR 或加密文件。SSE 当前传送运行进度和最终答案，不逐 token 推送。持久化的是对话消息与引用；每轮智能体的计划、临时文件和子智能体状态不跨请求恢复。

参考：[DeepAgents SDK](https://github.com/langchain-ai/deepagents)、[官方文档](https://docs.langchain.com/oss/python/deepagents/overview)、[PandaWiki](https://github.com/chaitin/PandaWiki)。
