"""Read committed Git trees without running repository code; archive filtered snapshots."""

import ast
import asyncio
import io
import logging
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import traceback
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4

logger = logging.getLogger(__name__)


class RepositoryFailure(ValueError):
    pass


EXTENSIONS = set(
    (
        ".py .js .jsx .ts .tsx .java .go .rs .c .h .cpp .hpp .cs .rb .php .swift .kt "
        ".scala .sh .sql .md .txt .rst .toml .yaml .yml .json .xml .html .css .vue .svelte "
        ".proto .graphql"
    ).split()
)
EXCLUDED = {
    "node_modules",
    "vendor",
    "dist",
    "build",
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".idea",
    ".next",
    "coverage",
}


def allowed_file(path):
    p = Path(path)
    if (
        len(path) > 512
        or re.search(r"[\x00-\x1f\x7f|\[\]]", path)
        or any(part in EXCLUDED for part in p.parts)
    ):
        return False
    name = p.name.lower()
    if name.startswith(".env") or name in {
        "package-lock.json",
        "yarn.lock",
        "uv.lock",
        "poetry.lock",
        ".npmrc",
        ".netrc",
        "credentials.json",
    }:
        return False
    if any(s in name for s in ("id_rsa", "id_ed25519")):
        return False
    return p.suffix.lower() in EXTENSIONS or name in {"dockerfile", "makefile", ".gitignore"}


class GitReader:
    def __init__(self, settings):
        self.settings = settings

    def environment(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "KBAGENT_"))}
        env.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_TERMINAL_PROMPT="0",
            GIT_SSL_NO_VERIFY="false",
        )
        return env

    def run(self, args, env=None):
        try:
            result = subprocess.run(
                [
                    "git",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "credential.helper=",
                    "-c",
                    "protocol.ext.allow=never",
                    "-c",
                    "http.followRedirects=false",
                    "-c",
                    "http.sslVerify=true",
                    *args,
                ],
                env=env or self.environment(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.settings.repository_git_timeout,
                check=True,
            )
            return result.stdout
        except (subprocess.SubprocessError, OSError) as exc:
            raise RepositoryFailure(
                "Git 操作失败，请检查地址、分支、只读 Token、目录权限或 TLS 证书"
            ) from exc

    def validate(self, kind, source, ref):
        if ref:
            self.run(["check-ref-format", "--branch", ref])
        if kind == "local":
            p = Path(source)
            if not p.is_absolute():
                raise RepositoryFailure("本地仓库需填写服务器上的绝对路径")
            p = p.resolve()
            if not any(
                p.is_relative_to(root.resolve()) for root in self.settings.repository_local_roots
            ):
                raise RepositoryFailure("目录不在 KBAGENT_REPOSITORY_LOCAL_ROOTS 配置的允许范围内")
            top = Path(
                self.run(["-C", str(p), "rev-parse", "--show-toplevel"]).decode().strip()
            ).resolve()
            if p != top:
                raise RepositoryFailure("请选择已 clone 的 Git 仓库根目录")
            return str(p)
        if kind != "gitlab":
            raise RepositoryFailure("仓库类型必须为 gitlab 或 local")
        expected = urlsplit(self.settings.gitlab_url.rstrip("/"))
        actual = urlsplit(source)
        if expected.scheme not in {"http", "https"} or not expected.hostname:
            raise RepositoryFailure("请先在 .env 配置 KBAGENT_GITLAB_URL")
        prefix = expected.path.rstrip("/") + "/"
        decoded = unquote(actual.path)
        if (
            actual.scheme != expected.scheme
            or actual.hostname != expected.hostname
            or actual.port != expected.port
            or actual.username
            or actual.password
            or actual.query
            or actual.fragment
            or not decoded.startswith(prefix)
            or any(part in {".", ".."} for part in decoded.split("/"))
            or "\\" in decoded
            or re.search(r"[\x00-\x20\x7f]", decoded)
        ):
            raise RepositoryFailure("仓库 URL 必须属于配置的 GitLab 地址，且不得包含密码或额外参数")
        relative = decoded[len(prefix) :].removesuffix(".git").strip("/")
        if len(relative.split("/")) < 2 or not all(
            re.fullmatch(r"[\w.-]+", p) for p in relative.split("/")
        ):
            raise RepositoryFailure("请输入完整 GitLab 项目 URL（含组名和项目名）")
        return source.rstrip("/")

    def read(self, repo):
        source = self.validate(repo["kind"], repo["source"], repo["ref"])
        with tempfile.TemporaryDirectory(prefix="kbagent-git-") as directory:
            env = self.environment()
            if repo["kind"] == "gitlab":
                helper = Path(directory) / "askpass"
                helper.write_text(
                    "#!/bin/sh\nexec "
                    + __import__("shlex").quote(sys.executable)
                    + ' -m kbagent.git_askpass "$@"\n'
                )
                helper.chmod(0o700)
                env.update(
                    GIT_ASKPASS=str(helper),
                    PYTHONPATH=str(Path(__file__).resolve().parent.parent),
                    KBAGENT_GITLAB_USERNAME=self.settings.gitlab_username,
                    KBAGENT_GITLAB_TOKEN=self.settings.gitlab_token.get_secret_value(),
                )
                if self.settings.gitlab_ssl_ca:
                    env["GIT_SSL_CAINFO"] = str(self.settings.gitlab_ssl_ca)
            if repo["kind"] == "local":
                # Read objects directly: no local upload-pack, checkout, filters or hooks.
                destination = source
                revision = "refs/heads/" + repo["ref"] if repo["ref"] else "HEAD"
                commit = (
                    self.run(
                        [
                            "-C",
                            destination,
                            "rev-parse",
                            "--verify",
                            "--end-of-options",
                            revision + "^{commit}",
                        ]
                    )
                    .decode()
                    .strip()
                )
            else:
                destination = str(Path(directory) / "repo")
                args = [
                    "-c",
                    "protocol.file.allow=never",
                    "clone",
                    "--bare",
                    "--depth=1",
                    "--single-branch",
                ]
                if repo["ref"]:
                    args += ["--branch", repo["ref"]]
                self.run([*args, "--", source, destination], env)
                commit = self.run(["-C", destination, "rev-parse", "HEAD"]).decode().strip()
            tree = self.run(["-C", destination, "ls-tree", "-r", "-l", "-z", commit])
            files, skipped, total = [], 0, 0
            for record in tree.split(b"\0"):
                if not record:
                    continue
                meta, rawpath = record.split(b"\t", 1)
                mode, kind, oid, size = meta.split()
                try:
                    path = rawpath.decode("utf-8")
                except UnicodeError:
                    skipped += 1
                    continue
                if (
                    mode not in {b"100644", b"100755"}
                    or kind != b"blob"
                    or not allowed_file(path)
                    or int(size) > self.settings.repository_max_file_bytes
                    or len(files) >= self.settings.repository_max_files
                    or total + int(size) > self.settings.repository_max_total_bytes
                ):
                    skipped += 1
                    continue
                data = self.run(["-C", destination, "cat-file", "blob", oid.decode()])
                try:
                    content = data.decode("utf-8")
                except UnicodeError:
                    skipped += 1
                    continue
                if (
                    not content.strip()
                    or "\0" in content
                    or re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", content)
                ):
                    skipped += 1
                    continue
                files.append(dict(path=path, content=content))
                total += len(data)
            if not files:
                raise RepositoryFailure("仓库没有可导入的文本代码或文档，请检查分支及过滤限制")
            return commit, files, skipped


def structure_wiki(repo, commit, files, ids, skipped):
    modules = {}
    for f in files:
        module = f["path"].split("/")[0] if "/" in f["path"] else "根目录"
        modules.setdefault(module, []).append(f)
    intro = (
        f"# {repo['name']} · 代码 Wiki\n\n提交：`{commit}`\n\n"
        "这是依据代码树生成的结构索引，非 AI 架构分析。"
        "仅包含已提交、通过过滤的文本文件。\n\n"
        f"导入 {len(files)} 个文件，跳过 {skipped} 个文件。\n\n"
    )
    pages = {}
    for module, entries in sorted(modules.items()):
        body = f"# 模块：{module}\n\n提交：`{commit}`\n\n"
        for f in entries:
            body += (
                f"## {f['path']}\n\n[[id:{ids[f['path']]}|{f['path']}]] · "
                f"{len(f['content'].splitlines())} 行\n\n"
            )
            if f["path"].endswith(".py"):
                try:
                    tree = ast.parse(f["content"])
                    symbols = [
                        f"{type(n).__name__} {n.name} · L{n.lineno}"
                        for n in ast.walk(tree)
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    ][:50]
                    if symbols:
                        body += "\n".join("- " + s for s in symbols) + "\n\n"
                except SyntaxError:
                    pass
        pages["模块 · " + module] = body
        intro += (
            f"## {module}\n\n"
            + "\n".join(f"- [[id:{ids[f['path']]}|{f['path']}]]" for f in entries)
            + "\n\n"
        )
    pages["仓库概览"] = intro
    return pages


class RepositoryService:
    def __init__(self, store, objects, settings):
        self.store, self.objects, self.settings = store, objects, settings
        self.git = GitReader(settings)
        self.stopping = False

    def sync(self, repo):
        commit, files, skipped = self.git.read(repo)
        snapshot = dict(
            id=uuid4().hex, object_key=f"repositories/{repo['space_id']}/{uuid4().hex}.tar.gz"
        )
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as tar:
            for f in files:
                data = f["content"].encode()
                entry = tarfile.TarInfo(f["path"])
                entry.size = len(data)
                tar.addfile(entry, io.BytesIO(data))
        data = archive.getvalue()
        self.store.reserve_object(snapshot["object_key"])
        try:
            self.objects.client.put_object(
                self.settings.minio_bucket,
                snapshot["object_key"],
                io.BytesIO(data),
                len(data),
                content_type="application/gzip",
            )
            self.store.save_repository_snapshot(
                repo, commit, files, skipped, snapshot, structure_wiki
            )
        except Exception:
            try:
                self.store.abandon_object(snapshot["object_key"])
                self.objects.collect(self.store)
            except Exception as cleanup_error:
                logger.warning("Repository cleanup deferred (%s)", type(cleanup_error).__name__)
            raise

    def source(self, space_id, repository_id, path, snapshot_id=None, start_line=1, end_line=200):
        snapshot = self.store.repository_snapshot(space_id, repository_id, snapshot_id)
        if not snapshot:
            raise KeyError("代码快照不存在或不属于当前知识库")
        response = self.objects.download(snapshot["object_key"])
        try:
            archive = io.BytesIO(response.read())
        finally:
            response.close()
            response.release_conn()
        with tarfile.open(fileobj=archive, mode="r:gz") as tar:
            try:
                member = tar.getmember(path)
            except KeyError as exc:
                raise KeyError("此版本中不存在该文件") from exc
            content = tar.extractfile(member).read().decode()
        lines = content.splitlines()
        end_line = min(end_line, start_line + 499, len(lines))
        return dict(
            path=path,
            snapshot_id=snapshot["id"],
            commit_sha=snapshot["commit_sha"],
            start_line=start_line,
            end_line=end_line,
            line_count=len(lines),
            content="\n".join(lines[start_line - 1 : end_line]),
        )

    async def worker(self):
        while not self.stopping:
            try:
                repo = await asyncio.to_thread(self.store.claim_repository_job)
            except Exception as exc:
                logger.warning("Repository queue temporarily unavailable (%s)", type(exc).__name__)
                await asyncio.sleep(2)
                continue
            if not repo:
                await asyncio.sleep(0.5)
                continue
            try:
                if repo["job"] == "sync":
                    await asyncio.to_thread(self.sync, repo)
                else:
                    from .repository_wiki import generate_wiki

                    await generate_wiki(self.store, self.settings, repo)
            except Exception as exc:
                from .repository_errors import repository_error

                message = repository_error(exc, repo["job"], self.settings.agent_timeout)
                frames = traceback.extract_tb(exc.__traceback__)
                location = " > ".join(
                    f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}" for frame in frames
                )
                # Exception strings, bodies, arguments and locals can expose credentials.
                logger.warning(
                    "Repository job failed repository=%s job=%s exception=%s at=%s",
                    repo["id"],
                    repo["job"],
                    type(exc).__name__,
                    location,
                )
                try:
                    await asyncio.to_thread(
                        self.store.repository_failure, repo["id"], repo["job"], message[:500]
                    )
                except Exception as failure:
                    logger.warning(
                        "Repository failure recording deferred (%s)", type(failure).__name__
                    )
