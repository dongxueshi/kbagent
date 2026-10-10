# 架构说明

```mermaid
flowchart LR
    Browser[中文管理界面] --> API[FastAPI]
    API --> Import[文本 / PDF / DOCX 解析]
    Import --> DB[(SQLite 文档与会话)]
    DB --> Index[中文分词 + FTS5 BM25]
    API --> Mode{已配置模型?}
    Mode -->|否| Retrieval[返回原文片段]
    Mode -->|是| Deep[DeepAgents 主智能体]
    Deep --> Research[知识检索子智能体]
    Deep --> Tools[搜索与阅读工具]
    Research --> Tools
    Tools --> Index
    Tools --> DB
    Deep --> Scratch[StateBackend 虚拟工作区]
    Retrieval --> SSE[SSE 进度与最终答案]
    Deep --> SSE
    SSE --> Browser
```

## 数据与索引

`spaces → documents → chunks` 保存知识库、原文与片段。分片长度 900 字符、重叠 120 字符；中文通过 jieba 搜索分词，使用 FTS5 BM25 排序，标题权重为正文的三倍。没有嵌入模型、外部向量数据库或网络索引依赖。

新建、修改、删除文档在同一 SQLite 事务中维护片段和 FTS 表，避免孤立或陈旧索引。WAL 模式支持本地读写。检索结果返回片段、文档 ID 与标题，不把 BM25 排名当作概率或准确率。

`conversations → messages` 按知识库保存历史，只在回答成功后一次性提交用户和助手消息。未来删除或编辑原文会改变引用链接对应的内容；引用不是不可变的文档版本快照。

## 智能体执行

使用固定版本 `deepagents==0.7.23` 的 `create_deep_agent`，显式传入 `ChatOpenAI`，通过兼容 Chat Completions 接口连接模型。SDK 提供规划、子智能体、上下文管理与虚拟文件工具。模型可在复杂问题中调用 `task`，委派给 `knowledge-researcher`，该子智能体持有同样的知识库范围约束。

`search_knowledge(query)` 和 `read_document(document_id, offset)` 通过服务器闭包绑定当前知识库；模型不能选择其他知识库 ID。读取原文每次最多 6000 字符。`StateBackend()` 是虚拟状态文件系统，不挂载宿主目录，不提供 shell 执行能力。

最近六轮完成的问答进入上下文，旧引用必须重新检索。工具实际读取的文档进入本次证据集合；答案中的 `[资料:文档ID]` 仅能映射到本次证据。非法文档 ID 引用标为「未核实引用」，没有检索行为的回答不作为知识问答展示。该校验确认引用访问来源，不证明模型的每句结论都被证据支持，仍需用户核对。

SDK 图是每次请求新建的；历史由应用数据库管理。此版本没有 LangGraph 持久化 checkpointer，因此任务中断不会恢复中间图状态。

## 本地服务

FastAPI 同时提供 REST、SSE 和静态资源。前端不从 CDN 拉取依赖，用户文本和模型输出通过转义或 DOM `textContent` 展示，不执行上传 HTML。文件不写入用户提供的路径；解析后仅保存文本。默认监听本机，未实现身份认证。

导入限制涵盖上传大小、提取字符数、PDF 页数、DOCX 解压总大小。解析仍在服务进程的工作线程中执行；处理不可信的大规模文件需要后续独立任务队列和资源隔离。当前面向可信单用户本地使用。

## 后续扩展方向

- 团队使用：认证、RBAC、审计日志、租户配额。
- 更大语料：后台导入任务、嵌入模型、向量/词法混合召回和重排。
- Wiki 产品：树形文档、版本历史、独立阅读站、发布流程。
- 智能体能力：AI 创作、持续任务检查点、流式 token、人工审批。
- 数据来源：OCR、受限 URL 抓取、Sitemap/RSS、第三方平台连接。
