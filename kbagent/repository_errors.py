"""Actionable repository failures without leaking provider bodies or credentials."""

import openai
import pymysql
from langgraph.errors import GraphRecursionError
from minio.error import S3Error
from urllib3.exceptions import HTTPError


def repository_error(exc, job, timeout):
    """Classify known failures; never return the raw exception message or response body."""
    # Imported lazily to keep the repository service independent of this classifier.
    from .repositories import RepositoryFailure

    if isinstance(exc, RepositoryFailure):
        return str(exc)
    if isinstance(exc, pymysql.MySQLError):
        code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
        reasons = {
            1045: "MySQL 账号认证失败，请检查数据库用户名与密码",
            1049: "MySQL 数据库不存在，请检查数据库名称",
            2003: "无法连接 MySQL，请检查地址、端口和服务状态",
            2006: "MySQL 连接中断，请检查服务状态与连接超时设置",
            2013: "MySQL 操作期间连接中断，请检查网络和超时设置",
            1205: "MySQL 事务等待锁超时，请稍后重试",
            1213: "MySQL 事务发生死锁，请重试",
            1146: "MySQL 数据表缺失，请重启服务完成表初始化",
            1406: "MySQL 写入内容超过字段限制，请检查仓库文件大小",
        }
        return reasons.get(code, "MySQL 保存仓库数据失败，请检查数据库服务与写入权限")
    if isinstance(exc, S3Error):
        reasons = {
            "AccessDenied": "MinIO 拒绝访问，请检查访问密钥与存储桶读写权限",
            "InvalidAccessKeyId": "MinIO 访问密钥无效，请检查存储配置",
            "SignatureDoesNotMatch": "MinIO 签名认证失败，请检查私有密钥与服务时间",
            "NoSuchBucket": "MinIO 存储桶不存在，请检查存储桶配置",
            "NoSuchKey": "MinIO 源码快照不存在，请检查备份与存储数据是否一致",
        }
        return reasons.get(exc.code, "MinIO 保存或读取源码快照失败，请检查存储服务")
    if isinstance(exc, openai.AuthenticationError):
        return "模型认证失败（HTTP 401），请检查 KBAGENT_API_KEY"
    if isinstance(exc, openai.PermissionDeniedError):
        return "模型服务拒绝访问（HTTP 403），请检查账号、模型权限或访问限制"
    if isinstance(exc, openai.NotFoundError):
        return "模型接口或模型不存在（HTTP 404），请检查 KBAGENT_BASE_URL 和 KBAGENT_MODEL"
    if isinstance(exc, openai.RateLimitError):
        return "模型服务限流或额度不足（HTTP 429），请检查配额并稍后重试"
    if isinstance(exc, openai.APITimeoutError):
        return "模型单次请求超时，请检查模型服务响应速度或缩小分析范围"
    if isinstance(exc, openai.APIConnectionError):
        return "无法连接模型服务，请检查 KBAGENT_BASE_URL、网络与 TLS 证书"
    if isinstance(exc, openai.BadRequestError):
        body = exc.body if isinstance(exc.body, dict) else {}
        error = body.get("error", body)
        code = error.get("code") if isinstance(error, dict) else None
        if code == "context_length_exceeded":
            return "模型上下文长度超限，请使用更大上下文的模型或减少仓库导入文件"
        return "模型请求被拒绝（HTTP 400），请检查模型名称、上下文长度及工具调用支持"
    if isinstance(exc, openai.APIStatusError):
        return "模型服务返回错误，请检查服务状态并稍后重试"
    if isinstance(exc, GraphRecursionError):
        return "AI Wiki 智能体达到步骤上限，请缩小仓库分析范围后重试"
    if isinstance(exc, TimeoutError):
        if job == "wiki":
            return (
                f"AI Wiki 生成超时（任务上限 {timeout} 秒），请缩小分析范围，"
                "或在 .env 增加 KBAGENT_AGENT_TIMEOUT 后重启"
            )
        return "仓库同步超时，请检查 Git 或存储服务响应速度"
    if isinstance(exc, HTTPError):
        return "无法连接 MinIO，请检查地址、端口、网络与 TLS 证书"
    if job == "wiki":
        return "AI Wiki 生成失败，请查看服务端 Repository job failed 日志定位异常"
    return "代码仓库同步失败，请查看服务端 Repository job failed 日志定位异常"
