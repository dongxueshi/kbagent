"""Run an isolated browser workflow; requires a system Chromium or Playwright browser."""

import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from chat_browser_checks import check_chat
from playwright.sync_api import expect, sync_playwright
from repository_browser_checks import check_repositories
from test_storage import isolated_storage
from wiki_browser_checks import check_wiki

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "test-results"


def run():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, KBAGENT_API_KEY="", KBAGENT_MODEL="", KBAGENT_PORT=str(port))
    OUTPUT.mkdir(exist_ok=True)
    with (
        tempfile.TemporaryDirectory(prefix="kbagent-browser-repo-") as repository_root,
        isolated_storage() as settings,
    ):
        checkout = Path(repository_root) / "service"
        checkout.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main", str(checkout)], check=True, capture_output=True
        )
        (checkout / "orders.py").write_text(
            "# 订单升级策略\ndef forceUpgrade(order):\n    return order['version'] < 2\n"
        )
        subprocess.run(["git", "-C", str(checkout), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(checkout),
                "-c",
                "user.name=Browser",
                "-c",
                "user.email=browser@example.invalid",
                "commit",
                "-m",
                "Fixture",
            ],
            check=True,
            capture_output=True,
        )
        env["KBAGENT_REPOSITORY_LOCAL_ROOTS"] = __import__("json").dumps([repository_root])
        env["KBAGENT_MYSQL_DATABASE"] = settings.mysql_database
        env["KBAGENT_MINIO_BUCKET"] = settings.minio_bucket
        with tempfile.TemporaryFile(mode="w+") as logs:
            server = subprocess.Popen(
                ["bash", "scripts/start.sh"],
                cwd=ROOT,
                env=env,
                stdout=logs,
                stderr=subprocess.STDOUT,
            )
            try:
                url = f"http://127.0.0.1:{port}"
                for _ in range(200):
                    try:
                        urllib.request.urlopen(url + "/api/health", timeout=1).close()
                        break
                    except OSError:
                        if server.poll() is not None:
                            raise RuntimeError("Test server exited during startup")
                        time.sleep(0.1)
                else:
                    raise RuntimeError("Test server did not become healthy")
                with sync_playwright() as pw:
                    chromium = os.environ.get("CHROMIUM_PATH") or shutil.which("chromium")
                    browser = pw.chromium.launch(
                        **({"executable_path": chromium} if chromium else {}),
                        args=["--no-sandbox"],
                    )
                    page = browser.new_page(viewport={"width": 1440, "height": 1000})
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(url)
                    page.get_by_role("heading", name="欢迎来到 KB Agent").wait_for()
                    page.get_by_role("button", name="加载示例知识库").click()
                    page.get_by_role("heading", name="产品与团队手册", exact=True).wait_for()
                    page.screenshot(path=OUTPUT / "overview.png", full_page=True)
                    page.locator('nav [data-view="documents"]').click()
                    page.get_by_role("button", name="＋ 新建文档", exact=True).click()
                    page.locator('#document-form input[name="title"]').fill("浏览器测试部署指南")
                    page.locator("#editor-content").fill(
                        "# 部署指南\n\n离线环境通过 uv sync 安装依赖。\n"
                        "<script>window.injectionExecuted = true</script>"
                    )
                    page.get_by_role("button", name="预览", exact=True).click()
                    assert page.locator("#editor-preview h1").inner_text() == "部署指南"
                    assert page.evaluate("window.injectionExecuted") is None
                    page.get_by_role("button", name="保存文档", exact=True).click()
                    page.locator("table button[data-doc]").filter(
                        has_text="浏览器测试部署指南"
                    ).wait_for()
                    page.get_by_role("button", name="＋ 新建文档", exact=True).click()
                    page.locator('#document-form input[name="title"]').fill("取消的文档")
                    page.locator("#editor-content").fill("不应保存")
                    page.get_by_role("button", name="关闭", exact=True).click()
                    assert page.locator("table button[data-doc]").count() == 4
                    page.locator("#search-input").fill("离线环境")
                    page.locator("#search-input").press("Enter")
                    page.locator(".search-hit").filter(has_text="浏览器测试部署指南").wait_for()
                    page.locator(".search-hit").first.click()
                    page.locator("#document-preview").wait_for()
                    with page.expect_download() as download:
                        page.get_by_role("link", name="↓ 导出 Markdown").click()
                    exported = Path(download.value.path()).read_text()
                    assert "离线环境通过 uv sync" in exported
                    page.get_by_role("button", name="编辑文档", exact=True).click()
                    page.locator("#editor-content").fill(
                        "# 新部署指南\n\n离线环境通过 uv sync 安装更新。"
                    )
                    page.get_by_role("button", name="保存文档", exact=True).click()
                    page.locator("#dialog").wait_for(state="hidden")
                    page.locator("#file-input").set_input_files(
                        {
                            "name": "浏览器导入.txt",
                            "mimeType": "text/plain",
                            "buffer": "导入文档包含持久化数据库说明。".encode(),
                        }
                    )
                    page.locator("table button[data-doc]").filter(has_text="浏览器导入").wait_for()
                    page.locator("table button[data-doc]").filter(has_text="浏览器导入").click()
                    with page.expect_download() as original:
                        page.get_by_role("link", name="↓ 下载原件").click()
                    assert (
                        Path(original.value.path()).read_bytes()
                        == "导入文档包含持久化数据库说明。".encode()
                    )
                    page.get_by_role("button", name="关闭", exact=True).click()
                    page.locator('nav [data-view="chat"]').click()
                    page.locator("#question").fill("离线环境")
                    page.locator("#question").press("Enter")
                    page.locator(".message-text").filter(has_text="并非 AI 生成答案").wait_for()
                    assert (
                        page.locator(".source-chip").filter(has_text="浏览器测试部署指南").count()
                        == 1
                    )
                    page.locator("#send").wait_for(state="visible")
                    page.screenshot(path=OUTPUT / "chat.png", full_page=True)
                    page.get_by_role("button", name="＋ 新对话", exact=True).click()
                    page.get_by_role("heading", name="从一个好问题开始").wait_for()
                    page.locator(".history-item").first.click()
                    page.locator(".source-chip").first.wait_for()
                    page.locator(".source-chip").first.click()
                    expect(page.locator("#document-preview")).to_contain_text("新部署指南")
                    page.get_by_role("button", name="关闭", exact=True).click()
                    page.locator('nav [data-view="settings"]').click()
                    page.get_by_role("heading", name="连接模型服务").wait_for()
                    assert page.get_by_text("尚未配置", exact=True).count() == 1
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.locator('nav [data-view="overview"]').click()
                    page.get_by_role("heading", name="产品与团队手册", exact=True).wait_for()
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= window.innerWidth"
                    ), "Mobile layout overflows viewport"
                    page.screenshot(path=OUTPUT / "mobile.png", full_page=True)
                    check_chat(page, url, OUTPUT)
                    check_wiki(page, url, OUTPUT)
                    check_repositories(page, checkout, OUTPUT, settings)
                    assert not errors, errors
                    browser.close()
                    print(
                        "Browser workflow passed: demo, edit, preview, search, import, "
                        "export, chat, sources, history, settings, mobile, no JS errors."
                    )
            except Exception:
                logs.seek(0)
                print(logs.read()[-3000:])
                raise
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


if __name__ == "__main__":
    run()
