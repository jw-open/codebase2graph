from __future__ import annotations

import re
from pathlib import Path

from .models import Graph
from .scanner import iter_files, read_text

SECRET_VALUE_RE = re.compile(
    r"(?i)\b(AWS_SECRET_ACCESS_KEY|AWS_ACCESS_KEY_ID|ANTHROPIC_API_KEY|OPENAI_API_KEY|"
    r"GITHUB_TOKEN|SLACK_BOT_TOKEN|STRIPE_SECRET_KEY|PASSWORD|SECRET|TOKEN|PRIVATE_KEY)\b"
    r"\s*[:=]\s*['\"]?([^'\"\s#]+)"
)
PLAINTEXT_URL_RE = re.compile(r"http://(?!localhost|127\.0\.0\.1)([A-Za-z0-9_.-]+)")
WILDCARD_IAM_RE = re.compile(r'(?s)(Action|Resource)\s*=\s*(\[\s*)?["\']\*["\']')


def build_security_graph(root: Path) -> Graph:
    graph = Graph()
    graph.add_node("security-root", "Security", attributes={"kind": "security_root", "path": "."})

    for path in iter_files(root):
        rel = path.relative_to(root).as_posix()
        name = path.name
        text = read_text(path, limit=500_000)
        _add_secret_risks(graph, rel, text)
        _add_transport_risks(graph, rel, text)

        if name in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}:
            _add_compose_risks(graph, rel, text)
        if name == "Dockerfile" or name.endswith(".Dockerfile"):
            _add_dockerfile_risks(graph, rel, text)
        if path.suffix == ".tf":
            _add_terraform_risks(graph, rel, text)
        if path.suffix in {".yml", ".yaml"}:
            _add_kubernetes_risks(graph, rel, text)

    return graph


def _add_secret_risks(graph: Graph, rel: str, text: str) -> None:
    for index, match in enumerate(SECRET_VALUE_RE.finditer(text), start=1):
        key, value = match.group(1), match.group(2)
        if value.startswith("${") or value.lower() in {"changeme", "example", "placeholder", "none", "null"}:
            continue
        severity = "critical" if any(token in key.upper() for token in ("SECRET", "TOKEN", "PRIVATE_KEY")) else "high"
        _add_risk(
            graph,
            rel,
            f"secret:{index}:{key.lower()}",
            f"Hardcoded {key}",
            "hardcoded_secret",
            severity,
            {"secret_key": key, "redacted_value_prefix": value[:4]},
        )


def _add_transport_risks(graph: Graph, rel: str, text: str) -> None:
    for index, match in enumerate(PLAINTEXT_URL_RE.finditer(text), start=1):
        _add_risk(
            graph,
            rel,
            f"http:{index}:{match.group(1)}",
            f"Plain HTTP to {match.group(1)}",
            "plaintext_http",
            "medium",
            {"host": match.group(1)},
        )


def _add_compose_risks(graph: Graph, rel: str, text: str) -> None:
    if re.search(r"privileged:\s*true", text):
        _add_risk(graph, rel, "compose:privileged", "Privileged container", "privileged_container", "high")
    if re.search(r":latest['\"]?\s*(?:$|#)", text, re.M):
        _add_risk(graph, rel, "compose:latest", "Unpinned latest container tag", "unpinned_image", "medium")
    if re.search(r"0\.0\.0\.0:", text):
        _add_risk(graph, rel, "compose:public-bind", "Service bound to all interfaces", "public_bind", "medium")


def _add_dockerfile_risks(graph: Graph, rel: str, text: str) -> None:
    if re.search(r"^USER\s+root\s*$", text, re.M) or not re.search(r"^USER\s+", text, re.M):
        _add_risk(graph, rel, "docker:user", "Container may run as root", "container_root_user", "medium")
    if re.search(r"^FROM\s+\S+:latest\s*$", text, re.M):
        _add_risk(graph, rel, "docker:latest", "Dockerfile uses latest base image", "unpinned_image", "medium")


def _add_terraform_risks(graph: Graph, rel: str, text: str) -> None:
    if WILDCARD_IAM_RE.search(text):
        _add_risk(graph, rel, "terraform:wildcard-iam", "Wildcard IAM permission", "wildcard_iam", "high")
    if re.search(r"acl\s*=\s*['\"]public-read", text):
        _add_risk(graph, rel, "terraform:public-s3", "Public S3 ACL", "public_storage", "high")
    if re.search(r"(encrypted|storage_encrypted)\s*=\s*false", text):
        _add_risk(graph, rel, "terraform:unencrypted", "Cloud storage encryption disabled", "unencrypted_storage", "high")
    if re.search(r"cidr_blocks\s*=\s*\[\s*['\"]0\.0\.0\.0/0['\"]", text):
        _add_risk(graph, rel, "terraform:open-cidr", "Security group allows 0.0.0.0/0", "open_network", "high")


def _add_kubernetes_risks(graph: Graph, rel: str, text: str) -> None:
    if re.search(r"privileged:\s*true", text):
        _add_risk(graph, rel, "k8s:privileged", "Privileged Kubernetes workload", "privileged_container", "high")
    if re.search(r"runAsUser:\s*0", text):
        _add_risk(graph, rel, "k8s:root", "Kubernetes workload runs as root", "container_root_user", "medium")


def _add_risk(
    graph: Graph,
    rel: str,
    suffix: str,
    label: str,
    risk_type: str,
    severity: str,
    extra: dict[str, str] | None = None,
) -> None:
    source_id = f"security:source:{rel}"
    graph.add_node(source_id, Path(rel).name, attributes={"kind": "security_source", "path": rel})
    graph.add_edge("security-root", source_id, "scans")
    risk_id = f"security:risk:{rel}:{suffix}"
    attrs = {"kind": "security_risk", "risk_type": risk_type, "severity": severity, "path": rel}
    attrs.update(extra or {})
    graph.add_node(risk_id, label, attributes=attrs)
    graph.add_edge(source_id, risk_id, "has_risk")
