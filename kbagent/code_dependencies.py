"""Conservative static import links. These are dependencies, never a runtime call graph."""

import ast
import posixpath
import re
from pathlib import PurePosixPath


def javascript_imports(content):
    # Tokenize strings/comments first so examples inside comments or strings are excluded.
    tokens = []
    pattern = (
        r"//[^\n]*|/\*[\s\S]*?\*/|`(?:\\.|[^`\\])*`|\"(?:\\.|[^\"\\])*\""
        r"|'(?:\\.|[^'\\])*'|[A-Za-z_$][\w$]*|[^\s]"
    )
    for match in re.finditer(pattern, content):
        value = match[0]
        if value.startswith(("//", "/*")):
            continue
        tokens.append(value)
    for index, token in enumerate(tokens):
        tail = tokens[index + 1 : index + 3]
        literal = None
        if token in {"from", "import"} and tail:
            literal = tail[0]
        elif token == "require" and len(tail) == 2 and tail[0] == "(":
            literal = tail[1]
        if literal and literal.startswith(("'", '"')) and literal[1:2] == ".":
            yield literal[1:-1]


def import_links(files, docs):
    paths = {f["path"] for f in files}
    python_modules = {}
    for path in sorted(paths):
        if path.endswith(".py"):
            name = path.removesuffix(".py").replace("/", ".").removesuffix(".__init__")
            python_modules.setdefault(name, []).append(path)
    links = set()
    for f in files:
        path, content = f["path"], docs[f["document_id"]]["content"]
        if path.endswith(".py"):
            try:
                tree = ast.parse(content)
            except (SyntaxError, ValueError, RecursionError):
                continue
            package = list(PurePosixPath(path).parent.parts)
            if package == ["."]:
                package = []
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level > len(package) + 1:
                        continue
                    prefix = package[: len(package) - node.level + 1] if node.level else []
                    module = ".".join([*prefix, *([node.module] if node.module else [])])
                    names = [module, *(module + "." + a.name for a in node.names)]
                for name in names:
                    matches = python_modules.get(name, [])
                    target = matches[0] if len(matches) == 1 else None
                    if target and target != path:
                        links.add((path, target))
        elif path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
            # Only relative literal imports; aliases and computed imports remain unresolved.
            for imported in javascript_imports(content):
                base = posixpath.normpath(posixpath.join(posixpath.dirname(path), imported))
                candidates = [
                    base,
                    *(base + ext for ext in (".ts", ".tsx", ".js", ".jsx")),
                    *(base + "/index" + ext for ext in (".ts", ".tsx", ".js", ".jsx")),
                ]
                matches = [p for p in candidates if p in paths]
                target = base if base in paths else matches[0] if len(matches) == 1 else None
                if target and target != path:
                    links.add((path, target))
    return sorted(links)
