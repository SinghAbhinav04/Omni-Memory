"""Extract a code graph from source files — symbols, line ranges, and edges.

Two backends produce the *same* schema:
  - tree-sitter (multi-language, the flagship) when installed
  - Python's stdlib `ast` (zero-dep fallback, Python only)

So `pip install omni-memory-agent` gives the deep multi-language graph, while the
bundled/plugin engine still gets a real Python graph with no dependencies.

A file extract is a dict:
  {
    "symbols": [ {id, kind, name, file, line_start, line_end, parent} ],
    "calls":   [ {src, name, line} ],       # src = enclosing symbol id (unresolved callee)
    "imports": [ {file, module, names} ],
    "bases":   [ {cls, name} ],             # class id -> base name (unresolved)
  }
Cross-file resolution of `calls`/`bases` names to symbol ids happens in build.py.

Symbol id scheme:  "<relpath>"            for a file
                   "<relpath>::<qualname>"  for a symbol  (e.g. "a/b.py::A.m")
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Optional

# extension -> tree-sitter language name
LANGUAGES = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx",
    ".go": "go", ".rs": "rust", ".java": "java", ".rb": "ruby",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp",
}

# Per-language node-type spec for the tree-sitter walker. New languages slot in
# here without touching the walker logic. `bases_field` = a child field holding
# superclasses; `bases_type` = a child node type to scan for them (JS heritage).
_JS_LIKE = {
    "func": {"function_declaration", "method_definition",
             "generator_function_declaration"},
    "class": {"class_declaration"},
    "name_field": "name",
    "call": {"call_expression"}, "call_field": "function",
    "import": {"import_statement"},
    "bases_field": None, "bases_type": "class_heritage",
}
_TS_SPEC = {
    "python": {
        "func": {"function_definition"},
        "class": {"class_definition"},
        "name_field": "name",
        "call": {"call"}, "call_field": "function",
        "import": {"import_statement", "import_from_statement"},
        "bases_field": "superclasses", "bases_type": None,
    },
    "javascript": _JS_LIKE,
    "typescript": {**_JS_LIKE,
                   "class": {"class_declaration", "abstract_class_declaration"}},
}
_TS_SPEC["tsx"] = _TS_SPEC["typescript"]

_SKIP_DIRS = {"node_modules", ".git", ".omni-memory", "dist", "build",
              "__pycache__", ".venv", "venv", "env", "target", ".tox",
              ".mypy_cache", ".pytest_cache", ".ruff_cache"}
_MAX_BYTES = 1_500_000


def available() -> bool:
    """True if the tree-sitter multi-language backend is installed."""
    try:
        import tree_sitter_language_pack  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _empty(rel: str, nlines: int) -> dict:
    return {"symbols": [{"id": rel, "kind": "file", "name": rel.split("/")[-1],
                         "file": rel, "line_start": 1, "line_end": max(1, nlines),
                         "parent": None, "signature": "", "doc": "", "raises": []}],
            "calls": [], "imports": [], "bases": []}


# call names that look like "this fires an event / message / side-effect
# downstream" — surfaced as Emits/Publishes in the symbol dossier.
_EMIT_HINTS = ("publish", "emit", "produce", "dispatch", "send", "enqueue",
               "notify", "broadcast", "trigger", "post", "commit")


def is_emit(name: str) -> bool:
    n = (name or "").lower()
    return any(h in n for h in _EMIT_HINTS)


def extract_file(path: Path, root: Path) -> Optional[dict]:
    """Extract one file's code graph, or None if it isn't a supported language."""
    lang = LANGUAGES.get(path.suffix.lower())
    if not lang:
        return None
    try:
        if path.stat().st_size > _MAX_BYTES:
            return None
        src = path.read_text(errors="ignore")
    except Exception:  # noqa: BLE001
        return None
    rel = str(path.relative_to(root))
    if available() and lang in _TS_SPEC:
        try:
            return _extract_treesitter(rel, src, lang)
        except Exception:  # noqa: BLE001
            pass  # fall through to ast for python; else give an empty file node
    if lang == "python":
        return _extract_python_ast(rel, src)
    return _empty(rel, src.count("\n") + 1)


def extract_repo(root: Path, max_files: int = 5000) -> dict:
    """Walk the repo and merge every file's extract into one graph payload."""
    out = {"symbols": [], "calls": [], "imports": [], "bases": [],
           "backend": "tree-sitter" if available() else "ast",
           "files_parsed": 0}
    n = 0
    for p in sorted(root.rglob("*")):
        if n >= max_files:
            break
        if not p.is_file() or _SKIP_DIRS & set(p.parts):
            continue
        fx = extract_file(p, root)
        if fx is None:
            continue
        n += 1
        for k in ("symbols", "calls", "imports", "bases"):
            out[k].extend(fx[k])
    out["files_parsed"] = n
    return out


def _first_line(s: Optional[str]) -> str:
    if not s:
        return ""
    line = s.strip().splitlines()[0].strip()
    return line[:200]


def _ast_signature(fn) -> str:
    """A readable `(params)` string from a FunctionDef, incl. defaults/annotations
    at a light touch (names + *args/**kwargs + defaults marker)."""
    a = fn.args
    parts: list[str] = []
    posonly = getattr(a, "posonlyargs", [])
    for arg in posonly + a.args:
        parts.append(arg.arg)
    if posonly:
        parts.insert(len(posonly), "/")
    if a.vararg:
        parts.append("*" + a.vararg.arg)
    elif a.kwonlyargs:
        parts.append("*")
    for arg in a.kwonlyargs:
        parts.append(arg.arg)
    if a.kwarg:
        parts.append("**" + a.kwarg.arg)
    sig = "(" + ", ".join(parts) + ")"
    ret = getattr(fn, "returns", None)
    if ret is not None:
        try:
            sig += " -> " + ast.unparse(ret)  # py3.9+
        except Exception:  # noqa: BLE001
            pass
    return sig


def _ast_raises(fn) -> list[str]:
    """Exception type names raised directly in the function body."""
    out: list[str] = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Raise) and n.exc is not None:
            exc = n.exc
            target = exc.func if isinstance(exc, ast.Call) else exc
            name = getattr(target, "id", None) or getattr(target, "attr", None)
            if name and name not in out:
                out.append(name)
    return out


# ── stdlib ast backend (Python, zero-dep) ──────────────────────────────────
def _extract_python_ast(rel: str, src: str) -> dict:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return _empty(rel, src.count("\n") + 1)
    r = _empty(rel, src.count("\n") + 1)
    file_id = rel

    def sym_id(stack: list[str]) -> str:
        return f"{rel}::{'.'.join(stack)}"

    def call_name(node: ast.AST) -> Optional[str]:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    def base_name(node: ast.AST) -> Optional[str]:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    def visit(node: ast.AST, parent_id: str, stack: list[str], in_class: bool):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                nstack = stack + [child.name]
                sid = sym_id(nstack)
                is_class = isinstance(child, ast.ClassDef)
                kind = "class" if is_class else ("method" if in_class else "function")
                r["symbols"].append({
                    "id": sid, "kind": kind, "name": child.name, "file": rel,
                    "line_start": child.lineno,
                    "line_end": getattr(child, "end_lineno", child.lineno),
                    "parent": parent_id,
                    "signature": _ast_signature(child) if not is_class else "",
                    "doc": _first_line(ast.get_docstring(child)),
                    "raises": _ast_raises(child)})
                if is_class:
                    for b in child.bases:
                        bn = base_name(b)
                        if bn:
                            r["bases"].append({"cls": sid, "name": bn})
                visit(child, sid, nstack, is_class)
            else:
                # collect calls made directly inside `node` (attribute them to it)
                for sub in ast.walk(child):
                    if isinstance(sub, ast.Call):
                        cn = call_name(sub.func)
                        if cn:
                            r["calls"].append({"src": parent_id, "name": cn,
                                               "line": getattr(sub, "lineno", 0)})
                    elif isinstance(sub, ast.Import):
                        for a in sub.names:
                            r["imports"].append({"file": rel, "module": a.name, "names": []})
                    elif isinstance(sub, ast.ImportFrom):
                        r["imports"].append({"file": rel, "module": sub.module or "",
                                             "names": [a.name for a in sub.names]})

    visit(tree, file_id, [], False)
    return r


# ── tree-sitter backend (multi-language, flagship) ─────────────────────────
def _extract_treesitter(rel: str, src: str, lang: str) -> dict:
    from tree_sitter_language_pack import get_parser
    spec = _TS_SPEC[lang]
    parser = get_parser(lang)
    root = parser.parse(bytes(src, "utf8")).root_node
    r = _empty(rel, src.count("\n") + 1)
    file_id = rel

    def text(n) -> str:
        return n.text.decode("utf8", "ignore")

    def name_of(n) -> Optional[str]:
        f = n.child_by_field_name(spec["name_field"])
        return text(f) if f else None

    def callee_name(n) -> Optional[str]:
        # the function/method being called: foo(...) -> foo, a.b.foo(...) -> foo,
        # self.m(...) -> m. Matches the ast backend so the graph is backend-agnostic.
        if n is None:
            return None
        if n.type in ("identifier",):
            return text(n)
        # attribute (python) / member_expression (js/ts): take the trailing name
        for f in ("attribute", "property", "field"):
            a = n.child_by_field_name(f)
            if a is not None:
                return text(a)
        ids = [c for c in _descendants(n) if c.type in ("identifier", "property_identifier")]
        return text(ids[-1]) if ids else None

    def signature_of(fn) -> str:
        p = fn.child_by_field_name("parameters")
        if p is None:  # js/ts formal_parameters, others: first param-ish child
            p = next((k for k in fn.children
                      if k.type.endswith("parameters") or k.type == "parameter_list"), None)
        sig = text(p).replace("\n", " ").strip() if p is not None else "()"
        rt = fn.child_by_field_name("return_type") or fn.child_by_field_name("type")
        if rt is not None:
            sig += " -> " + text(rt).strip()
        return sig[:200]

    def doc_of(fn) -> str:
        body = fn.child_by_field_name("body")
        if body is None:
            return ""
        for c in body.children:  # python: first stmt is an expression_statement>string
            if c.type in ("expression_statement", "comment"):
                s = next((d for d in _descendants(c) if d.type == "string"), None)
                if s is not None or c.type == "comment":
                    return _first_line(text(s if s is not None else c).strip("\"'`# \t/*"))
            if c.type not in ("comment",):
                break
        return ""

    def raises_of(fn) -> list[str]:
        out: list[str] = []
        for d in _descendants(fn):
            if d.type in ("raise_statement", "throw_statement"):
                names = [text(k) for k in _descendants(d)
                         if k.type in ("identifier", "type_identifier")]
                if names and names[0] not in out:
                    out.append(names[0])
        return out

    def walk(n, parent_id: str, stack: list[str], in_class: bool):
        for c in n.children:
            if c.type in spec["func"] or c.type in spec["class"]:
                nm = name_of(c) or "?"
                nstack = stack + [nm]
                sid = f"{rel}::{'.'.join(nstack)}"
                is_class = c.type in spec["class"]
                kind = "class" if is_class else ("method" if in_class else "function")
                r["symbols"].append({
                    "id": sid, "kind": kind, "name": nm, "file": rel,
                    "line_start": c.start_point[0] + 1,
                    "line_end": c.end_point[0] + 1, "parent": parent_id})
                if is_class:
                    bf = None
                    if spec.get("bases_field"):
                        bf = c.child_by_field_name(spec["bases_field"])
                    if bf is None and spec.get("bases_type"):
                        bf = next((k for k in c.children
                                   if k.type == spec["bases_type"]), None)
                    if bf is not None:
                        for b in _descendants(bf):
                            if b.type in ("identifier", "type_identifier"):
                                r["bases"].append({"cls": sid, "name": text(b)})
                walk(c, sid, nstack, is_class)
            else:
                if c.type in spec["call"]:
                    fn = c.child_by_field_name(spec["call_field"])
                    cn = callee_name(fn)
                    if cn:
                        r["calls"].append({"src": parent_id, "name": cn,
                                           "line": c.start_point[0] + 1})
                if c.type in spec["import"]:
                    r["imports"].append({"file": rel, "module": text(c).strip(),
                                         "names": []})
                walk(c, parent_id, stack, in_class)

    walk(root, file_id, [], False)
    return r


def _descendants(n):
    stack = list(n.children)
    while stack:
        c = stack.pop()
        yield c
        stack.extend(c.children)
