"""Obsidian-style link workflows exercised in a real browser."""

from playwright.sync_api import expect


def check_wiki(page, url, output):
    page.set_viewport_size({"width": 1440, "height": 1000})
    spaces = page.request.get(url + "/api/spaces").json()
    base = url + "/api/spaces/" + spaces[0]["id"]

    def create(title, content):
        response = page.request.post(base + "/documents", data={"title": title, "content": content})
        assert response.status == 201
        return response.json()

    target = create("浏览器关联目标", "# 目标\n\n## 安装\n启动服务的说明。")
    create("同名资料", "第一个同名文档")
    create("同名资料", "第二个同名文档")
    page.locator('nav [data-view="documents"]').click()
    page.get_by_role("button", name="＋ 新建文档", exact=True).click()
    page.locator('#document-form input[name="title"]').fill("浏览器知识入口")
    page.locator('#document-form input[name="aliases"]').fill("知识入口别名")
    editor = page.locator("#editor-content")
    editor.fill("# 入口\n\n😀 参考 ")
    editor.press("End")
    editor.press_sequentially("[[浏览器关联")
    expect(page.locator("#wiki-completions")).to_be_visible()
    expect(page.locator("#wiki-completions button")).to_have_count(1)
    editor.press("Enter")
    assert "[[浏览器关联目标]]" in editor.input_value()
    editor.fill(
        "# 入口\n\n😀 参考 [[浏览器关联目标#安装|部署资料]]。\n\n"
        "未知 [[待补充主题]]。\n\n同名 [[同名资料]]。\n\n"
        "`[[忽略示例]]`\n\n```\n[[代码示例]]\n```"
    )
    page.get_by_role("button", name="保存文档", exact=True).click()
    source_button = page.locator("table button[data-doc]").filter(has_text="浏览器知识入口")
    source_button.wait_for()
    source_button.click()
    expect(page.locator("#document-preview .wiki-link")).to_have_count(3)
    expect(page.locator("#document-preview")).to_contain_text("😀 参考")
    page.get_by_role("button", name="部署资料", exact=True).click()
    expect(page.locator("#dialog .dialog-head h2")).to_have_text("浏览器关联目标")
    expect(page.locator("#document-preview h2")).to_have_text("安装")
    backlink = page.locator(".knowledge-panel .relation-item").filter(has_text="浏览器知识入口")
    expect(backlink).to_contain_text("部署资料")
    page.screenshot(path=output / "backlinks.png", full_page=True)
    backlink.click()
    page.get_by_role("button", name="◈ 局部知识图谱", exact=True).click()
    expect(page.locator("#graph-summary")).to_contain_text("2 个节点 · 1 条关联")
    expect(page.locator("#local-graph canvas").first).to_be_visible()
    page.get_by_role("button", name="放大图谱", exact=True).click()
    page.get_by_role("button", name="缩小图谱", exact=True).click()
    page.get_by_role("button", name="适应视图", exact=True).click()
    page.locator("#graph-filter").fill("浏览器关联目标")
    page.locator("#graph-filter").fill("")

    # Click a rendered Cytoscape node using its actual layout position.
    point = page.evaluate("""() => {
        const container = document.querySelector('#local-graph');
        const cy = container._cyreg.cy;
        const node = cy.nodes().filter(n => n.data('label') === '浏览器关联目标')[0];
        const p = node.renderedPosition(), box = container.getBoundingClientRect();
        return {x: box.x + p.x, y: box.y + p.y};
    }""")
    page.mouse.click(point["x"], point["y"])
    expect(page.locator("#graph-selection")).to_contain_text("浏览器关联目标")
    page.locator("#graph-selection button").click()
    expect(page.locator("#dialog .dialog-head h2")).to_have_text("浏览器关联目标")
    page.locator(".knowledge-panel .relation-item").filter(has_text="浏览器知识入口").click()

    page.get_by_role("button", name="待补充主题", exact=True).click()
    page.get_by_role("button", name="＋ 创建 待补充主题", exact=True).click()
    expect(page.locator('#document-form input[name="title"]')).to_have_value("待补充主题")
    page.locator("#editor-content").fill("# 待补充主题\n\n由链接创建的资料。")
    page.get_by_role("button", name="保存文档", exact=True).click()
    expect(page.locator("#dialog .dialog-head h2")).to_have_text("浏览器知识入口")
    expect(page.locator("#document-preview .wiki-link.unresolved")).to_have_count(0)
    page.locator("#document-preview").get_by_role("button", name="同名资料", exact=True).click()
    expect(page.locator("#dialog .dialog-head h2")).to_have_text("选择链接目标")
    expect(page.locator(".link-candidates button")).to_have_count(2)
    page.locator(".link-candidates button").first.click()
    expect(page.locator("#document-preview .wiki-link.ambiguous")).to_have_count(0)

    page.get_by_role("button", name="部署资料", exact=True).click()
    page.get_by_role("button", name="编辑文档", exact=True).click()
    page.locator('#document-form input[name="title"]').fill("浏览器关联目标新版")
    page.get_by_role("button", name="保存文档", exact=True).click()
    page.locator("table button[data-doc]").filter(has_text="浏览器知识入口").click()
    expect(page.get_by_role("button", name="部署资料", exact=True)).to_have_attribute(
        "title", "打开 浏览器关联目标新版 · 安装"
    )
    create("第二层关联", "[[浏览器关联目标新版]]")
    page.get_by_role("button", name="◈ 局部知识图谱", exact=True).click()
    expect(page.locator("#graph-summary")).to_contain_text("4 个节点")
    page.locator("#graph-depth").select_option("2")
    expect(page.locator("#graph-summary")).to_contain_text("5 个节点")
    page.screenshot(path=output / "knowledge-graph.png", full_page=True)

    page.get_by_role("button", name="关闭", exact=True).click()
    page.once("dialog", lambda popup: popup.accept())
    page.locator("tr").filter(has_text="浏览器关联目标新版").locator("[data-delete-doc]").click()
    expect(
        page.locator("table button[data-doc]").filter(has_text="浏览器关联目标新版")
    ).to_have_count(0)
    replacement = create("浏览器关联目标", "# 安装\n新的独立资料。")
    assert replacement["id"] != target["id"]
    page.locator("table button[data-doc]").filter(has_text="浏览器知识入口").click()
    expect(page.locator("#document-preview .wiki-link.broken")).to_have_count(1)
    page.get_by_role("button", name="部署资料", exact=True).click()
    expect(page.locator("#dialog .dialog-head h2")).to_have_text("修复失效链接")
    page.locator(".link-candidates button").first.click()
    expect(page.locator("#document-preview .wiki-link.broken")).to_have_count(0)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=output / "wiki-mobile.png", full_page=True)
    page.get_by_role("button", name="关闭", exact=True).click()
    page.reload()
    page.locator('nav [data-view="documents"]').click()
    page.locator("table button[data-doc]").filter(has_text="浏览器知识入口").click()
    expect(page.locator("#document-preview .wiki-link.resolved")).to_have_count(3)
    page.get_by_role("button", name="部署资料", exact=True).click()
    expect(page.locator("#dialog .dialog-head h2")).to_have_text("浏览器关联目标")
    print(
        "Wiki browser checks passed: completion, anchors, backlinks, node clicks, "
        "graph depth, pending creation, ambiguity, rename, deletion repair, reload, mobile."
    )
