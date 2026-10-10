"""Parse wiki links while preserving source offsets and excluding Markdown code."""

import re

LINK = re.compile(r"(?<!!)\[\[([^\[\]\n]+)\]\]")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def parse_links(content: str) -> list[dict]:
    # Mask excluded regions with spaces, retaining newlines and absolute offsets.
    chars = list(content)

    def mask(start, end):
        for i in range(start, end):
            if chars[i] != "\n":
                chars[i] = " "

    # Scan in source order: a comment marker inside code is literal, and vice versa.
    offset = 0
    while offset < len(content):
        if offset == 0 or content[offset - 1] == "\n":
            line_end = content.find("\n", offset)
            line_end = len(content) if line_end < 0 else line_end + 1
            line = content[offset:line_end]
            mark = FENCE.match(line)
            if mark:
                fence = mark[1]
                end = line_end
                while end < len(content):
                    next_end = content.find("\n", end)
                    next_end = len(content) if next_end < 0 else next_end + 1
                    next_line = content[end:next_end]
                    closing = FENCE.match(next_line)
                    end = next_end
                    if (
                        closing
                        and closing[1][0] == fence[0]
                        and len(closing[1]) >= len(fence)
                        and not next_line[closing.end() :].strip()
                    ):
                        break
                mask(offset, end)
                offset = end
                continue
            if line.startswith(("    ", "\t")):
                mask(offset, line_end)
                offset = line_end
                continue
        if content[offset] == "\\":
            offset += 2
            continue
        if content.startswith("<!--", offset):
            end = content.find("-->", offset + 4)
            end = len(content) if end < 0 else end + 3
            mask(offset, end)
            offset = end
            continue
        if content[offset] == "`":
            run_end = offset + 1
            while run_end < len(content) and content[run_end] == "`":
                run_end += 1
            closing = re.search(
                r"(?<!`)" + re.escape(content[offset:run_end]) + r"(?!`)", content[run_end:]
            )
            if closing:
                end = run_end + closing.end()
                mask(offset, end)
                offset = end
                continue
            offset = run_end
            continue
        offset += 1

    links = []
    for match in LINK.finditer("".join(chars)):
        # An odd run of preceding backslashes escapes the opening bracket.
        slash_start = match.start()
        while slash_start and content[slash_start - 1] == "\\":
            slash_start -= 1
        if (match.start() - slash_start) % 2:
            continue
        destination, _, label = match[1].partition("|")
        name, _, anchor = destination.partition("#")
        name, anchor = name.strip(), anchor.strip()
        if not name and not anchor:
            continue
        if len(name) > 200 or len(anchor) > 200 or len(label) > 200:
            continue
        start, end = match.span()
        links.append(
            {
                "target_name": name,
                "anchor": anchor,
                "label": label.strip(),
                "raw": content[start:end],
                "start": start,
                "end": end,
                "excerpt": content[max(0, start - 70) : min(len(content), end + 100)].strip(),
            }
        )
    return links
