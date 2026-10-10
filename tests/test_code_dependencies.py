from kbagent.code_dependencies import import_links


def links(content):
    files = [dict(path=path, document_id=path) for path in content]
    docs = {path: dict(content=body) for path, body in content.items()}
    return import_links(files, docs)


def test_python_relative_imports_and_ambiguous_modules():
    assert links({"src/main.py": "from .orders import upgrade", "src/orders.py": ""}) == [
        ("src/main.py", "src/orders.py")
    ]
    assert links({"main.py": "import pkg", "pkg.py": "", "pkg/__init__.py": ""}) == []
    assert links({"src/main.py": "from ...orders import upgrade", "orders.py": ""}) == []


def test_js_literals_ignore_examples_and_ambiguous_resolution():
    assert links(
        {
            "src/main.ts": "import {x} from './orders';\nrequire('./helper.js');\n"
            "// import './comment'\nconst example=\"import './string'\";",
            "src/orders.ts": "",
            "src/helper.js": "",
            "src/comment.ts": "",
            "src/string.ts": "",
        }
    ) == [("src/main.ts", "src/helper.js"), ("src/main.ts", "src/orders.ts")]
    assert links({"main.ts": "import './orders'", "orders.ts": "", "orders.js": ""}) == []
