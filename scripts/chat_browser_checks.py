"""Long-answer layout and safe rendering checks with deterministic browser SSE fixtures."""

import json

from playwright.sync_api import expect


def check_chat(page, url, output):
    page.set_viewport_size({"width": 1440, "height": 834})
    spaces = page.request.get(url + "/api/spaces").json()
    base = url + "/api/spaces/" + spaces[0]["id"]
    document = page.request.get(base + "/documents").json()[0]
    answer = (
        "## 强制升级配置与判断逻辑\n\n"
        "### 核心判断条件\n\n1. **强制升级开关必须开启**\n2. 策略使用 `versionCode` 判断。\n\n"
        + "\n\n".join(
            f"### 策略 {i}\n\n" + "这是完整的策略说明，包含判断条件和处理步骤。" * 8
            for i in range(1, 61)
        )
        + '\n\n**<img src=x onerror="window.chatInjection=true">**\n'
        + "\n```html\n<script>window.chatInjection=true</script>\n```\n"
        + "\nhttps://example.com/"
        + "long-path-" * 80
        + "\n\n完整回答结束标记"
    )
    source = {"document_id": document["id"], "title": document["title"]}
    event = {"type": "answer", "content": answer, "sources": [source], "mode": "agent"}

    def answer_fixture(route):
        route.fulfill(
            status=200,
            content_type="text/event-stream",
            body="data: " + json.dumps(event, ensure_ascii=False) + "\n\n",
        )

    page.route("**/conversations/*/chat", answer_fixture)
    page.locator('nav [data-view="chat"]').click()
    page.get_by_role("button", name="＋ 新对话", exact=True).click()
    page.locator("#question").fill("长回答布局测试")
    page.locator("#send").click()
    expect(page.locator(".message-text h2")).to_have_text("强制升级配置与判断逻辑")
    expect(page.locator(".message-text strong").first).to_have_text("强制升级开关必须开启")
    expect(page.locator(".message-text code")).to_have_text("versionCode")
    expect(page.locator("#send")).to_be_enabled()
    assert page.evaluate("window.chatInjection") is None
    assert page.locator(".message-text img, .message-text script").count() == 0

    def verify_layout():
        page.locator("#question").scroll_into_view_if_needed()
        geometry = page.evaluate("""() => {
            const panel = document.querySelector('.chat-panel').getBoundingClientRect();
            const input = document.querySelector('#chat-form').getBoundingClientRect();
            const list = document.querySelector('#chat-messages');
            const messages = list.getBoundingClientRect();
            return {
                inputInside: input.bottom <= panel.bottom + 1,
                separate: messages.bottom <= input.top + 1,
                height: list.clientHeight,
                scrolls: list.scrollHeight > list.clientHeight,
                noOverflow: document.documentElement.scrollWidth <= window.innerWidth,
            };
        }""")
        assert geometry["inputInside"] and geometry["separate"], geometry
        assert geometry["height"] > 80 and geometry["scrolls"], geometry
        assert geometry["noOverflow"], geometry
        page.locator("#chat-messages").evaluate("el => el.scrollTop = el.scrollHeight")
        expect(page.get_by_text("完整回答结束标记", exact=True)).to_be_in_viewport()
        expect(page.locator(".source-chip").last).to_be_in_viewport()
        expect(page.locator("#question")).to_be_in_viewport()
        expect(page.locator("#send")).to_be_in_viewport()
        page.locator("#chat-messages").evaluate("el => el.scrollTop = 0")
        expect(page.locator(".message-text h2")).to_be_in_viewport()
        expect(page.locator("#question")).to_be_in_viewport()

    for width, height in [(1440, 834), (1280, 500), (1920, 1080), (390, 844), (320, 568)]:
        page.set_viewport_size({"width": width, "height": height})
        verify_layout()
        if width in {1440, 390}:
            page.screenshot(path=output / f"chat-long-{width}.png", full_page=True)

    # Loading a history entry must apply the same rendering as the live SSE answer.
    conversation_id = page.locator(".history-item.active").get_attribute("data-conversation")
    history_path = base + "/conversations/" + conversation_id
    history = page.request.get(history_path).json()
    history["messages"] = [
        {"role": "user", "content": "长回答布局测试"},
        {"role": "assistant", "content": answer, "sources": [source], "mode": "agent"},
    ]
    page.route(history_path, lambda route: route.fulfill(json=history))
    page.get_by_role("button", name="＋ 新对话", exact=True).click()
    page.locator(f'[data-conversation="{conversation_id}"]').click()
    expect(page.locator(".message-text h2")).to_have_text("强制升级配置与判断逻辑")
    verify_layout()
    page.unroute(history_path)
    page.unroute("**/conversations/*/chat", answer_fixture)
    print(
        "Long chat checks passed: SSE/history Markdown, safe text, scroll to both ends, "
        "visible composer and sources, desktop/short/mobile screens."
    )
