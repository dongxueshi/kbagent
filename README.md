# KB Agent

基于 **DeepAgents SDK** 的本地知识库智能体系统。产品流程参考 [PandaWiki](https://github.com/chaitin/PandaWiki) 的知识库、文档管理和问答体验，独立实现；没有复制其代码或素材。

当前版本面向**本地单用户开发**，无需模型密钥即可运行。未配置模型时提供原文检索，配置模型后启用真正的 DeepAgents 工具调用。

## 已实现

- 中文 Web 工作台，实时文档、片段和字符统计。
- 多知识库创建、切换和删除；文档与会话按知识库隔离。
- Markdown 新建、编辑、基础安全预览和导出。
- Markdown / TXT / PDF / DOCX 导入，单文件默认最大 10 MB，单文档最大 200,000 字符。
- 中文分词 + SQLite FTS5 / BM25，无需嵌入模型，文档更新和删除时事务性维护索引。
- DeepAgents 主智能体、知识检索子智能体、规划、`search_knowledge` / `read_document` 工具，以及虚拟文件工作区。
- 问答过程通过 SSE 显示工具执行进度，完成后返回答案和可点击的文档引用。
- SQLite 持久化文档、会话和已完成的问答；失败请求不会保存半个对话轮次。
- 不配置模型时，返回明确标注的原文片段，不假冒 AI 答案。

## 本地启动

要求 Python **3.12 或 3.13**、[uv](https://docs.astral.sh/uv/getting-started/installation/)。Node 仅用于可选 JavaScript 语法检查，不是运行依赖。

```bash
cd /workspace/kbagent
bash scripts/setup.sh
bash scripts/start.sh
```

在本机浏览器访问端口 `8000`。服务默认只监听 `127.0.0.1`，健康检查为 `GET /api/health`，交互式 API 文档位于 `/docs`。云环境 onboarding 不提供 localhost 网页预览；在云机器中可使用本地 HTTP 请求验证。

首次启动没有虚构的知识库数据。点击「加载示例知识库」会创建三篇可编辑、可删除的示例文档；也可以直接创建空白知识库。

更换端口：`KBAGENT_PORT=8010 bash scripts/start.sh`。数据默认保存在项目的 `data/kbagent.sqlite3`，可通过 `KBAGENT_DATA_DIR` 更改。将 SQLite 的数据库、WAL 和 SHM 作为一个整体处理；备份推荐停止服务后复制整个数据目录。

`uv.lock` 固定依赖，`scripts/setup.sh` 使用冻结锁文件安装。云环境默认缓存目录为 `/workspace/.cache/uv`；其他机器可以覆盖：`UV_CACHE_DIR="$HOME/.cache/uv" bash scripts/setup.sh`。

## 稍后接入模型

复制 `.env.example` 为 `.env`，在本机填写自己的值；不要提交 `.env`。

```dotenv
KBAGENT_API_KEY=你的密钥
KBAGENT_MODEL=你的模型名称
KBAGENT_BASE_URL=https://api.openai.com/v1
```

也可以使用支持 OpenAI Chat Completions 和工具调用的兼容模型服务。以 DeepSeek 为例，接口地址可设置为 `https://api.deepseek.com`，模型名称需以服务商实际可用且支持工具调用的型号为准。配置后重启服务，再发起一次问答验证。没有配置模型时，无需这些变量。

密钥不返回给浏览器。模型调用失败时显示错误，不静默降级为检索。默认请求超时为 45 秒，完整智能体任务最多 120 秒 / 40 个图步骤，最多四个并发问答，同一会话不允许并发发送。云环境接入额外模型服务时，还需在环境设置中允许其域名并安全绑定凭据。

## 验证

```bash
make test
make lint
# 可选：已安装 Chromium 时，执行真实浏览器端到端检查
make browser
```

测试覆盖 CRUD、中文检索、索引更新/清理、知识库隔离、四种文件格式、大小/格式错误、SSE、会话持久化、重启、真实 DeepAgents 图的检索/阅读工具调用、引用校验和服务端文件隔离。

SDK 测试使用确定性模型测试替身驱动**真实 DeepAgents 图**，无需外部密钥；这不代表真实模型服务已连通。浏览器脚本启动隔离数据目录，结束后停止测试服务，不修改个人知识库。

## 架构与边界

详见 [架构说明](docs/architecture.md)。这是可运行的 MVP，尚未包含团队身份认证、RBAC、公开 Wiki 发布、向量检索、OCR、URL / Sitemap / RSS 导入、AI 辅助创作或第三方聊天平台集成。请保持本机监听，不直接作为多人公网服务部署。

基础预览支持标题、段落、列表行、引用与代码块，不执行原始 HTML，也不提供完整富文本编辑。PDF 暂不支持扫描件 OCR 或加密文件。SSE 当前传送运行进度和最终答案，不逐 token 推送。持久化的是对话消息与引用；每轮智能体的计划、临时文件和子智能体状态不跨请求恢复。

参考：[DeepAgents SDK](https://github.com/langchain-ai/deepagents)、[官方文档](https://docs.langchain.com/oss/python/deepagents/overview)、[PandaWiki](https://github.com/chaitin/PandaWiki)。
