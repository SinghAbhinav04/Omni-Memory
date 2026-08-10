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
import bisect
import re
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

# Directories that are never the project's own source — dependencies, build
# output, framework caches, generated bundles. Only used for the non-git
# fallback; inside a git repo we enumerate tracked files instead (below), which
# excludes everything .gitignore'd automatically.
_SKIP_DIRS = {"node_modules", ".git", ".omni-memory", "dist", "build", "out",
              "__pycache__", ".venv", "venv", "env", "target", ".tox",
              ".mypy_cache", ".pytest_cache", ".ruff_cache", ".idea", ".vscode",
              ".next", ".nuxt", ".svelte-kit", ".output", ".turbo", ".parcel-cache",
              ".cache", ".angular", ".vercel", ".netlify", "coverage",
              "vendor", "bower_components", "third_party", "generated", ".gradle"}
# Filenames that are generated/minified bundles, not hand-written source.
_SKIP_SUFFIXES = (".min.js", ".bundle.js", ".min.css", ".chunk.js", ".map",
                  ".generated.ts", ".g.dart")
_MAX_BYTES = 1_500_000
_MAX_LINE = 5000                # a line longer than this ⇒ minified/generated


def available() -> bool:
    """True if the tree-sitter multi-language backend is installed."""
    try:
        import tree_sitter_language_pack
        return True
    except Exception:
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


def _is_generated_name(path: Path) -> bool:
    n = path.name.lower()
    return any(n.endswith(s) for s in _SKIP_SUFFIXES)


def extract_file(path: Path, root: Path) -> Optional[dict]:
    """Extract one file's code graph, or None if it isn't hand-written source in a
    supported language (skips minified/generated bundles)."""
    lang = LANGUAGES.get(path.suffix.lower())
    if not lang or _is_generated_name(path):
        return None
    try:
        if path.stat().st_size > _MAX_BYTES:
            return None
        src = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        return None
    # minified/generated files pack everything onto a few enormous lines
    if src and max((len(ln) for ln in src.splitlines()), default=0) > _MAX_LINE:
        return None
    rel = str(path.relative_to(root))
    if available() and lang in _TS_SPEC:
        try:
            return _extract_treesitter(rel, src, lang)
        except Exception:  # noqa: BLE001
            pass  # fall through to a stdlib backend below
    if lang == "python":
        return _extract_python_ast(rel, src)
    if lang in _JS_LANGS:                          # dependency-free JS/TS graph
        try:
            return _extract_js_regex(rel, src)
        except Exception:  # noqa: BLE001
            pass
    return _empty(rel, src.count("\n") + 1)


# Languages the stdlib regex backend handles when tree-sitter isn't installed
# (so the Claude Code plugin, which can't pip-install anything, still graphs a
# TS/JS repo — approximately; tree-sitter via pip is exact).
_JS_LANGS = {"javascript", "typescript", "tsx"}


# In-process cache of per-file extracts, keyed by absolute path → (mtime_ns,
# size, extract). The long-running dashboard watcher rebuilds the whole graph on
# every save; parsing is the expensive step, so we re-parse ONLY files whose
# mtime/size changed and reuse the cached extract for everything else. Assembly
# (build.py) stays a full pass so cross-file call resolution remains correct.
_EXTRACT_CACHE: dict = {}


def _source_files(root: Path) -> list:
    """The project's OWN source files. Inside a git repo we ask git for tracked +
    new (non-ignored) files — so dependencies and build output (node_modules,
    .next, dist, vendored libs, …) are excluded exactly as .gitignore says, and
    we graph the code the developer actually wrote. Falls back to a filtered walk
    outside git."""
    try:
        from .. import gitmeta
        if gitmeta.is_repo(root):
            out = gitmeta._git(root, "ls-files", "--cached", "--others",
                               "--exclude-standard", "-z")
            if out:
                return [root / p for p in out.split("\0") if p]
    except Exception:  # noqa: BLE001
        pass
    return [p for p in root.rglob("*")
            if p.is_file() and not (_SKIP_DIRS & set(p.parts))]


def extract_repo(root: Path, max_files: int = 5000, incremental: bool = True) -> dict:
    """Merge every source file's extract into one graph payload — the project's
    own code only (git-tracked), never dependencies or build output.

    With `incremental` (default), unchanged files are served from the parse cache
    so only edited files are re-parsed — the watcher stays cheap on big repos."""
    out = {"symbols": [], "calls": [], "imports": [], "bases": [],
           "backend": "tree-sitter" if available() else "stdlib (ast + regex-js)",
           "files_parsed": 0, "reparsed": 0}
    n = 0
    seen: set = set()
    for p in sorted(_source_files(root)):
        if n >= max_files:
            break
        if not p.is_file() or _SKIP_DIRS & set(p.parts):
            continue
        if LANGUAGES.get(p.suffix.lower()) is None:
            continue
        key = str(p)
        try:
            st = p.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            continue
        cached = _EXTRACT_CACHE.get(key) if incremental else None
        if cached and cached[0] == stamp:
            fx = cached[1]
        else:
            fx = extract_file(p, root)
            if fx is None:
                continue
            if incremental:
                _EXTRACT_CACHE[key] = (stamp, fx)
            out["reparsed"] += 1
        seen.add(key)
        n += 1
        for k in ("symbols", "calls", "imports", "bases"):
            out[k].extend(fx[k])
    # forget deleted files so the cache can't leak or resurrect stale symbols
    for gone in set(_EXTRACT_CACHE) - seen:
        _EXTRACT_CACHE.pop(gone, None)
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


# ── stdlib regex backend (JS/TS/TSX, zero-dep, APPROXIMATE) ─────────────────
# So the Claude Code plugin (which can't pip-install tree-sitter) still gets a
# real code graph on a TypeScript/JavaScript repo. It's heuristic — arrow bodies
# without braces, decorators, and exotic syntax are imperfect. `pip install
# omni-memory-agent` (tree-sitter, Python >=3.10) is the exact path.
_JS_NOISE = re.compile(
    r"/\*.*?\*/|//[^\n]*|'(?:\\.|[^'\\\n])*'|\"(?:\\.|[^\"\\\n])*\"|`(?:\\.|[^`\\])*`",
    re.S)
_JS_CLASS = re.compile(
    r"\b(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)"
    r"(?:\s+extends\s+([A-Za-z_$][\w$.]*))?")
_JS_FUNC = re.compile(
    r"\b(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*"
    r"([A-Za-z_$][\w$]*)\s*(\([^)]*\))")
_JS_ARROW = re.compile(
    r"\b(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*"
    r"(?:async\s+)?(\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>")
_JS_FUNCEXPR = re.compile(
    r"\b(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
    r"(?:async\s+)?function\b\s*(\([^)]*\))?")
_JS_METHOD = re.compile(
    r"^[ \t]*(?:public|private|protected|static|readonly|abstract|override|async|get|set)?"
    r"[ \t]*(?:public|private|protected|static|readonly|abstract|override|async|get|set)?"
    r"[ \t]*([A-Za-z_$][\w$]*)[ \t]*(\([^;{]*\))[ \t]*(?::[^={;]+)?(?=\{)", re.M)
_JS_CALL = re.compile(r"([A-Za-z_$][\w$]*)\s*\(")
_JS_THROW = re.compile(r"\bthrow\s+(?:new\s+)?([A-Za-z_$][\w$]*)")
_JS_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "function",
                "await", "typeof", "new", "in", "of", "do", "else", "case",
                "super", "constructor", "require", "import", "class", "yield",
                "throw", "delete", "void", "with", "as"}


def _js_blank(src: str) -> str:
    """Replace comments/strings with spaces (keeping newlines) so brace matching
    and declaration detection don't trip on braces inside them."""
    return _JS_NOISE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), src)


def _first_comment_above(src_lines: list, start_line: int) -> str:
    """A one-line doc from the comment directly above a declaration (best-effort)."""
    j = start_line - 2                      # line above, 0-indexed
    while j >= 0 and not src_lines[j].strip():
        j -= 1
    if j < 0:
        return ""
    ln = src_lines[j].strip().lstrip("/* \t")
    return _first_line(ln) if ln and (src_lines[j].strip().startswith(("//", "*", "/*"))) else ""


def _extract_js_regex(rel: str, src: str) -> dict:
    r = _empty(rel, src.count("\n") + 1)
    clean = _js_blank(src)
    src_lines = src.split("\n")
    offsets = [0] + [i + 1 for i, ch in enumerate(clean) if ch == "\n"]

    def line_of(idx: int) -> int:
        return bisect.bisect_right(offsets, idx)

    def close_brace(open_idx: int) -> int:
        d, i, n = 0, open_idx, len(clean)
        while i < n:
            if clean[i] == "{":
                d += 1
            elif clean[i] == "}":
                d -= 1
                if d == 0:
                    return i
            i += 1
        return n - 1

    def next_brace(after: int, limit_line: int) -> int:
        # the '{' that opens this decl's body, if it's within a couple of lines
        i, n = after, len(clean)
        while i < n and line_of(i) <= limit_line + 2:
            if clean[i] == "{":
                return i
            if clean[i] == ";":
                return -1
            i += 1
        return -1

    syms = []   # (start_idx, end_idx, dict)

    def add_sym(name, kind, decl_idx, params, has_body_at):
        sl = line_of(decl_idx)
        ob = next_brace(has_body_at, sl)
        if ob == -1:                        # no brace body (e.g. `const f = () => x`)
            start_idx, end_idx, el = decl_idx, has_body_at, sl
        else:
            start_idx, cb = decl_idx, close_brace(ob)
            end_idx, el = cb, line_of(cb)
        sig = params if params else "()"
        raises = []
        body = clean[start_idx:end_idx + 1]
        for tm in _JS_THROW.finditer(body):
            if tm.group(1) not in raises:
                raises.append(tm.group(1))
        syms.append((start_idx, end_idx, {
            "kind": kind, "name": name, "file": rel,
            "line_start": sl, "line_end": el, "signature": sig[:200],
            "doc": _first_comment_above(src_lines, sl), "raises": raises}))

    for m in _JS_CLASS.finditer(clean):
        add_sym(m.group(1), "class", m.start(), "()", m.end())
        if m.group(2):
            r["bases"].append({"cls": None, "name": m.group(2).split(".")[-1],
                               "_clsname": m.group(1)})
    for m in _JS_FUNC.finditer(clean):
        add_sym(m.group(1), "function", m.start(), m.group(2), m.end())
    for m in _JS_ARROW.finditer(clean):
        add_sym(m.group(1), "function", m.start(),
                m.group(2) if m.group(2).startswith("(") else "()", m.end())
    for m in _JS_FUNCEXPR.finditer(clean):
        add_sym(m.group(1), "function", m.start(), m.group(2) or "()", m.end())
    for m in _JS_METHOD.finditer(clean):
        if m.group(1) not in _JS_KEYWORDS:
            add_sym(m.group(1), "method", m.start(1), m.group(2), m.end())

    # dedup by (name, start_line): the same decl can hit two patterns
    seen, uniq = set(), []
    for s in syms:
        key = (s[2]["name"], s[2]["line_start"])
        if key not in seen:
            seen.add(key)
            uniq.append(s)
    syms = sorted(uniq, key=lambda s: (s[0], -(s[1])))

    # assign ids + parent by innermost containment
    def parent_of(start_idx):
        best = None
        for st, en, d in syms:
            if st < start_idx <= en and (best is None or st > best[0]):
                best = (st, en, d)
        return best

    ids = {}
    for st, en, d in syms:
        p = parent_of(st)
        pid = ids.get((p[0], p[2]["name"])) if p else None
        qual = (p[2]["name"] + "." + d["name"]) if p else d["name"]
        sid = f"{rel}::{qual}"
        ids[(st, d["name"])] = sid
        d["id"], d["parent"] = sid, (pid or rel)
        d.setdefault("calls", [])
        r["symbols"].append(d)
        if d["kind"] == "class":
            for b in r["bases"]:
                if b.get("_clsname") == d["name"]:
                    b["cls"] = sid

    r["bases"] = [{"cls": b["cls"], "name": b["name"]}
                  for b in r["bases"] if b.get("cls")]

    # calls → attribute each to the innermost enclosing symbol
    for m in _JS_CALL.finditer(clean):
        name = m.group(1)
        if name in _JS_KEYWORDS:
            continue
        idx = m.start(1)
        inner = None
        for st, en, d in syms:
            if st <= idx <= en and (inner is None or st > inner[0]):
                inner = (st, en, d)
        if inner:
            r["calls"].append({"src": inner[2]["id"], "name": name,
                               "line": line_of(idx)})
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
        # The docstring is the block's first statement. Grammars differ: older
        # tree-sitter-python wraps it in an `expression_statement`, current ones
        # put the `string` node directly in the block. Handle both, plus a
        # leading comment, and read the quote-free `string_content` when present.
        for c in body.children:
            if c.type == "comment":
                return _first_line(text(c).strip("# \t/*"))
            if c.type in ("expression_statement", "string"):
                s = c if c.type == "string" else next(
                    (d for d in _descendants(c) if d.type == "string"), None)
                if s is None:
                    break
                sc = next((d for d in s.children if d.type == "string_content"), None)
                return _first_line(text(sc) if sc is not None
                                   else text(s).strip("\"'`# \t"))
            break  # first real statement isn't a docstring
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
                    "line_end": c.end_point[0] + 1, "parent": parent_id,
                    # same rich fields as the ast/regex backends (these were
                    # missing here, so the dossier had no params/doc/raises on
                    # tree-sitter installs — i.e. every Python ≥3.10 user)
                    "signature": "" if is_class else signature_of(c),
                    "doc": doc_of(c), "raises": raises_of(c)})
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
