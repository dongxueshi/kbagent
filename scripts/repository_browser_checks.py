"""Real browser coverage for the repository management and source citation workflow."""

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from playwright.sync_api import expect

from kbagent.deepwiki import generate_deep_wiki
from kbagent.store import Store


class BrowserWikiModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        prompt = next(m.content for m in reversed(messages) if isinstance(m, HumanMessage))
        ids = list(dict.fromkeys(re.findall(r"\[资料:([a-f0-9]{32})\]", prompt)))
        result = AIMessage(
            content="订单升级条件由版本字段决定。" + "".join(f"[资料:{cid}]" for cid in ids)
        )
        return ChatResult(generations=[ChatGeneration(message=result)])


def check_repositories(page, checkout, output, settings):
    if page.locator("#dialog").is_visible():
        page.get_by_role("button", name="关闭", exact=True).click()
    page.set_viewport_size({"width": 1440, "height": 834})
    page.locator('nav [data-view="repositories"]').click()
    page.get_by_role("button", name="＋ 添加仓库").click()
    page.locator('#repository-form [name="name"]').fill("浏览器代码库")
    page.locator('#repository-form [name="kind"]').select_option("local")
    page.locator('#repository-form [name="source"]').fill(str(checkout))
    page.locator('#repository-form [name="ref"]').fill("main")
    page.get_by_role("button", name="添加并同步").click()
    page.locator(".repository-card").filter(has_text="已同步").wait_for(timeout=30000)
    page.get_by_role("button", name="打开 Wiki", exact=True).click()
    page.get_by_role("button", name="仓库概览", exact=True).click()
    expect(page.locator("#document-preview")).to_contain_text("结构索引")
    assert page.get_by_role("button", name="编辑文档", exact=True).count() == 0
    page.get_by_role("button", name="关闭", exact=True).click()
    page.get_by_role("button", name="打开 Wiki", exact=True).click()
    page.locator("#repository-file-filter").fill("orders")
    code = page.locator("[data-repository-code]").filter(has_text="orders.py")
    code.click()
    expect(page.locator(".source-code")).to_contain_text("forceUpgrade")
    assert page.locator(".source-line").count() == 3
    page.screenshot(path=output / "repository-source.png", full_page=True)
    page.get_by_role("button", name="关闭", exact=True).click()
    page.locator('nav [data-view="chat"]').click()
    page.get_by_role("button", name="＋ 新对话", exact=True).click()
    page.locator("#question").fill("forceUpgrade")
    page.locator("#chat-form").get_by_role("button", name="发送").click()
    citation = page.locator(".source-chip[data-repository-code]").first
    citation.wait_for()
    citation.click()
    expect(page.locator(".source-code")).to_contain_text("forceUpgrade")
    page.get_by_role("button", name="关闭", exact=True).click()
    # Publish with a deterministic provider through the real SDK, then read it in Chromium.
    store = Store(settings)
    repo = next(r for s in store.spaces() for r in store.repositories(s["id"]))
    # Playwright's synchronous API already owns an event loop on this thread.
    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(
            asyncio.run,
            generate_deep_wiki(
                store, settings, repo, BrowserWikiModel(responses=[AIMessage(content="unused")])
            ),
        ).result()
    page.locator('nav [data-view="repositories"]').click()
    expect(page.locator(".repository-progress")).to_contain_text("生成完成")
    expect(page.locator(".repository-progress")).to_contain_text("文件 1/1")
    page.get_by_role("button", name="打开 Wiki", exact=True).click()
    page.get_by_role("button", name="AI 架构与关键流程", exact=True).click()
    expect(page.locator("#document-preview")).to_contain_text("已处理 1 / 1")
    page.locator("#document-preview [data-wiki-link]").filter(has_text="AI 模块 · .").click()
    expect(page.locator("#document-preview")).to_contain_text("文件文档")
    page.locator("#document-preview [data-wiki-link]").filter(
        has_text="AI 文件 · orders.py"
    ).click()
    expect(page.locator("#document-preview")).to_contain_text("分段 1")
    with page.expect_download() as exported:
        page.get_by_role("link", name="↓ 导出 Markdown").click()
    assert "订单升级条件" in Path(exported.value.path()).read_text()
    page.screenshot(path=output / "deepwiki-file.png", full_page=True)
    page.get_by_role("button", name="关闭", exact=True).click()
    for width in [390, 320]:
        page.set_viewport_size({"width": width, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.get_by_role("button", name="打开 Wiki", exact=True).click()
        page.locator("[data-repository-code]").first.click()
        expect(page.locator(".source-code")).to_contain_text("forceUpgrade")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.get_by_role("button", name="关闭", exact=True).click()
        expect(page.locator("#dialog")).not_to_be_visible()

    # Simulate separate historical sync and current AI failures, without a model request.
    def failed_status(route):
        response = route.fetch()
        repos = response.json()
        for repo in repos:
            repo["wiki_status"] = "error"
            repo["wiki_error"] = "AI Wiki 生成超时（任务上限 120 秒）"
            repo["error"] = "MySQL 连接中断"
        route.fulfill(response=response, json=repos)

    page.route("**/api/spaces/*/repositories", failed_status)
    try:
        page.locator('nav [data-view="chat"]').click()
        page.locator('nav [data-view="repositories"]').click()
        expect(page.locator(".repository-error").filter(has_text="AI Wiki 错误：")).to_be_visible()
        expect(page.locator(".repository-error").filter(has_text="同步错误：")).to_be_visible()
        expect(page.get_by_text("AI Wiki 生成失败 · 已同步代码仍可检索")).to_be_visible()
        expect(page.get_by_role("button", name="打开 Wiki", exact=True)).to_be_enabled()
    finally:
        page.unroute("**/api/spaces/*/repositories", failed_status)
    page.screenshot(path=output / "repository-mobile.png", full_page=True)
    print(
        "Repository browser checks passed: sync, full Wiki, progress, navigation, export, mobile."
    )
