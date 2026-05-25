from __future__ import annotations

import re
from pathlib import Path

from .models import Graph
from .scanner import iter_files, read_text

DECISION_EXTENSIONS = {
    ".md",
    ".mdx",
    ".rst",
    ".txt",
    ".adoc",
}

SOURCE_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".kts",
    ".swift",
    ".scala",
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".hpp",
    ".cs",
}

SECTION_KIND_BY_TITLE = {
    "problem": "design_problem",
    "context": "design_context",
    "motivation": "design_context",
    "goal": "design_goal",
    "goals": "design_goal",
    "requirement": "design_requirement",
    "requirements": "design_requirement",
    "option": "design_option",
    "options": "design_option",
    "alternative": "design_option",
    "alternatives": "design_option",
    "solution": "design_option",
    "solutions": "design_option",
    "pros": "design_pro",
    "advantages": "design_pro",
    "cons": "design_con",
    "disadvantages": "design_con",
    "tradeoff": "design_tradeoff",
    "tradeoffs": "design_tradeoff",
    "trade-offs": "design_tradeoff",
    "decision": "design_decision",
    "decisions": "design_decision",
    "status": "design_status",
    "consequence": "design_consequence",
    "consequences": "design_consequence",
}

SIGNAL_PATTERNS = {
    "design_problem": re.compile(r"\b(problem|pain|constraint|requirement|challenge)\b", re.IGNORECASE),
    "design_option": re.compile(r"\b(option|alternative|approach|solution)\b", re.IGNORECASE),
    "design_tradeoff": re.compile(r"\b(tradeoff|trade-off|trade off|pros?|cons?|cost|benefit|risk)\b", re.IGNORECASE),
    "design_decision": re.compile(r"\b(decision|decided|choose|chosen|therefore|we will|we use)\b", re.IGNORECASE),
}


def build_decision_graph(root: Path) -> Graph:
    graph = Graph()
    graph.add_node("decision-root", "Architecture Decisions", attributes={"kind": "decision_root", "path": "."})

    for path in iter_files(root):
        if path.suffix.lower() in DECISION_EXTENSIONS:
            _parse_decision_document(graph, root, path)
        elif path.suffix.lower() in SOURCE_EXTENSIONS:
            _parse_design_comments(graph, root, path)

    graph.current_node_id = "decision-root"
    return graph


def _parse_decision_document(graph: Graph, root: Path, path: Path) -> None:
    text = read_text(path, limit=500_000)
    if not text:
        return
    rel = path.relative_to(root).as_posix()
    lower_rel = rel.lower()
    is_decision_file = any(part in lower_rel for part in ("adr", "decision", "rfc", "design", "architecture"))
    sections = _markdown_sections(text)
    signal_sections = [(title, body) for title, body in sections if _section_kind(title)]
    if not is_decision_file and not signal_sections:
        return

    source_id = f"decision:source:{rel}"
    graph.add_node(
        source_id,
        path.name,
        attributes={
            "kind": "decision_source",
            "path": rel,
            "source_type": "document",
        },
        content=_clip(text),
    )
    graph.add_edge("decision-root", source_id, "contains")

    previous_problem_id: str | None = None
    last_option_id: str | None = None
    for index, (title, body) in enumerate(sections):
        kind = _section_kind(title)
        if not kind:
            continue
        label = _clean_title(title)
        node_id = f"decision:{kind}:{rel}:{index}:{_slug(label)}"
        graph.add_node(
            node_id,
            label,
            attributes={
                "kind": kind,
                "path": rel,
                "section": label,
            },
            content=_clip(body),
        )
        graph.add_edge(source_id, node_id, "documents")
        if kind == "design_problem":
            graph.add_edge("decision-root", node_id, "has_problem")
            previous_problem_id = node_id
        elif kind == "design_option":
            if previous_problem_id:
                graph.add_edge(previous_problem_id, node_id, "has_option")
            last_option_id = node_id
        elif kind in {"design_pro", "design_con", "design_tradeoff"} and last_option_id:
            graph.add_edge(last_option_id, node_id, "has_tradeoff")
        elif kind == "design_decision":
            if previous_problem_id:
                graph.add_edge(previous_problem_id, node_id, "resolved_by")
            if last_option_id:
                graph.add_edge(node_id, last_option_id, "chooses")

    for index, line in enumerate(_decision_signal_lines(text)):
        kind = _line_kind(line)
        node_id = f"decision:{kind}:{rel}:signal:{index}"
        graph.add_node(
            node_id,
            _line_label(line),
            attributes={
                "kind": kind,
                "path": rel,
                "source_type": "signal",
            },
            content=line,
        )
        graph.add_edge(source_id, node_id, "mentions")


def _parse_design_comments(graph: Graph, root: Path, path: Path) -> None:
    text = read_text(path, limit=500_000)
    if not text:
        return
    rel = path.relative_to(root).as_posix()
    comment_lines = _extract_comment_lines(text)
    signals = [line for line in comment_lines if _line_kind(line)]
    if not signals:
        return

    source_id = f"decision:source:{rel}"
    graph.add_node(
        source_id,
        path.name,
        attributes={
            "kind": "decision_source",
            "path": rel,
            "source_type": "code_comments",
        },
    )
    graph.add_edge("decision-root", source_id, "contains")
    for index, line in enumerate(signals):
        kind = _line_kind(line)
        node_id = f"decision:{kind}:{rel}:comment:{index}"
        graph.add_node(
            node_id,
            _line_label(line),
            attributes={
                "kind": kind,
                "path": rel,
                "source_type": "code_comment",
            },
            content=line,
        )
        graph.add_edge(source_id, node_id, "mentions")


def _markdown_sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current_title = "Document"
    current_body: list[str] = []
    heading_re = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
    for line in text.splitlines():
        match = heading_re.match(line)
        if match:
            if current_body:
                sections.append((current_title, "\n".join(current_body).strip()))
            current_title = match.group(2).strip()
            current_body = []
        else:
            current_body.append(line)
    if current_body:
        sections.append((current_title, "\n".join(current_body).strip()))
    return sections


def _section_kind(title: str) -> str | None:
    cleaned = _clean_title(title).lower()
    cleaned = re.sub(r"^\d+[\).:-]?\s*", "", cleaned)
    for key, kind in SECTION_KIND_BY_TITLE.items():
        if cleaned == key or cleaned.startswith(f"{key}:") or cleaned.startswith(f"{key} "):
            return kind
    return None


def _decision_signal_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip(" -*\t")
        if 20 <= len(line) <= 500 and _line_kind(line):
            lines.append(line)
    return lines[:80]


def _line_kind(line: str) -> str | None:
    for kind in ("design_decision", "design_tradeoff", "design_option", "design_problem"):
        if SIGNAL_PATTERNS[kind].search(line):
            return kind
    return None


def _extract_comment_lines(text: str) -> list[str]:
    lines: list[str] = []
    in_block = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "/*" in line:
            in_block = True
            line = line.split("/*", 1)[1]
        if in_block:
            cleaned = line.replace("*/", "").strip(" *")
            if cleaned:
                lines.append(cleaned)
            if "*/" in raw_line:
                in_block = False
            continue
        for prefix in ("#", "//", "--"):
            if line.startswith(prefix):
                lines.append(line[len(prefix) :].strip())
                break
    return lines


def _clean_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip().strip("#")).strip()


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip().lower()).strip("-")
    return slug[:80] or "section"


def _line_label(line: str) -> str:
    line = re.sub(r"\s+", " ", line).strip()
    return line[:96] + ("..." if len(line) > 96 else "")


def _clip(text: str, limit: int = 2000) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
