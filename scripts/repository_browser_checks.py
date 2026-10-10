"""Real browser coverage for the repository management and source citation workflow."""

from playwright.sync_api import expect


def check_repositories(page, checkout, output):
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
    page.locator('nav [data-view="repositories"]').click()
    for width in [390, 320]:
        page.set_viewport_size({"width": width, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.get_by_role("button", name="打开 Wiki", exact=True).click()
        page.locator("[data-repository-code]").first.click()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.get_by_role("button", name="关闭", exact=True).click()

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
    print("Repository browser checks passed: add, sync, Wiki, source lines, citations, mobile.")
