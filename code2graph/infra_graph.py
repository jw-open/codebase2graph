from __future__ import annotations

import json
import re
from pathlib import Path

from .models import Graph
from .scanner import iter_files, read_text

COMPOSE_NAMES = {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}
K8S_KINDS = {"Deployment", "Service", "StatefulSet", "DaemonSet", "Job", "CronJob", "Ingress"}
CI_DIR_PARTS = {".github/workflows", ".gitlab-ci.yml", "azure-pipelines.yml", "Jenkinsfile"}
URL_REF_RE = re.compile(r"https?://([A-Za-z0-9_.-]+)(?::\d+)?")
ENV_REF_RE = re.compile(r"\b([A-Z][A-Z0-9_]*(?:URL|URI|HOST|ENDPOINT))\s*[:=]\s*['\"]?([^'\"\s]+)")
PROVIDER_RE = re.compile(r"\bprovider\s+\"([A-Za-z0-9_-]+)\"")
RESOURCE_RE = re.compile(r"\bresource\s+\"([A-Za-z0-9_-]+)_([A-Za-z0-9_-]+)\"")
RESOURCE_BLOCK_RE = re.compile(r'\bresource\s+"([A-Za-z0-9_-]+)"\s+"([A-Za-z0-9_.-]+)"\s*\{(?P<body>.*?)\n\}', re.S)
ASSIGNMENT_RE = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=\s*(.+?)\s*$", re.M)

AWS_SERVICE_ALIASES = {
    "ami": "ec2",
    "autoscaling": "ec2",
    "cloudwatch": "cloudwatch",
    "db": "rds",
    "dynamodb": "dynamodb",
    "ebs": "ebs",
    "ec2": "ec2",
    "ecr": "ecr",
    "ecs": "ecs",
    "efs": "efs",
    "eks": "eks",
    "iam": "iam",
    "instance": "ec2",
    "lambda": "lambda",
    "lb": "elb",
    "rds": "rds",
    "s3": "s3",
    "secret": "secretsmanager",
    "secretsmanager": "secretsmanager",
    "security": "ec2_security_group",
    "vpc": "vpc",
}
CONFIG_KEYS = {
    "acl",
    "ami",
    "bucket",
    "cluster_name",
    "desired_capacity",
    "encrypted",
    "engine",
    "engine_version",
    "image",
    "instance_type",
    "name",
    "region",
    "repository_url",
    "size",
    "storage_encrypted",
    "version",
    "volume_size",
}


def build_infra_graph(root: Path) -> Graph:
    graph = Graph()
    graph.add_node("infra-root", "Infrastructure", attributes={"kind": "infra_root", "path": "."})
    graph.add_node("infra:environment:local", "Local", attributes={"kind": "deployment_environment", "environment": "local"})
    graph.add_edge("infra-root", "infra:environment:local", "has_environment")

    for path in iter_files(root):
        rel = path.relative_to(root).as_posix()
        name = path.name
        if name in COMPOSE_NAMES:
            _add_compose(graph, root, path)
        elif name == "Dockerfile" or name.endswith(".Dockerfile"):
            _add_dockerfile(graph, root, path)
        elif _looks_like_kubernetes(path):
            _add_kubernetes(graph, root, path)
        elif rel.startswith(".github/workflows/") and path.suffix in {".yml", ".yaml"}:
            _add_github_actions(graph, root, path)
        elif name in {"package.json", "requirements.txt", "pyproject.toml"}:
            _add_dependencies(graph, root, path)
        elif path.suffix == ".tf":
            _add_terraform(graph, root, path)
        elif name in {"serverless.yml", "serverless.yaml"}:
            _add_serverless(graph, root, path)

        if path.suffix in {".env", ".example", ".sample", ".yml", ".yaml", ".json", ".toml"} or name.startswith(".env"):
            _add_integration_refs(graph, root, path)

    return graph


def _add_compose(graph: Graph, root: Path, path: Path) -> None:
    rel = path.relative_to(root).as_posix()
    file_id = f"infra:compose:{rel}"
    graph.add_node(file_id, path.name, attributes={"kind": "compose_file", "path": rel})
    graph.add_edge("infra-root", file_id, "defines")

    text = read_text(path)
    services = _parse_compose_services(text)
    for service, attrs in services.items():
        service_id = f"infra:service:{service}"
        attrs_out = {"kind": "service", "source": "compose", "path": rel, "deployment": "local", "runtime": "docker_compose"}
        attrs_out.update({key: value for key, value in attrs.items() if isinstance(value, str) and value})
        graph.add_node(service_id, service, attributes=attrs_out)
        graph.add_edge(file_id, service_id, "defines_service")
        graph.add_edge("infra:environment:local", service_id, "runs")
        for port in attrs.get("ports", []):
            port_id = f"infra:port:{service}:{port}"
            graph.add_node(port_id, port, attributes={"kind": "port", "service": service, "path": rel})
            graph.add_edge(service_id, port_id, "exposes")
        for dep in attrs.get("depends_on", []):
            dep_id = f"infra:service:{dep}"
            graph.add_node(dep_id, dep, attributes={"kind": "service", "source": "compose", "path": rel, "deployment": "local"})
            graph.add_edge(service_id, dep_id, "depends_on")
        for ref in attrs.get("refs", []):
            target = _target_id(ref)
            graph.add_node(target, ref, attributes={"kind": "integration", "source": "compose", "path": rel})
            graph.add_edge(service_id, target, "communicates_with")


def _parse_compose_services(text: str) -> dict[str, dict[str, list[str] | str]]:
    services: dict[str, dict[str, list[str] | str]] = {}
    in_services = False
    current: str | None = None
    section: str | None = None
    for line in text.splitlines():
        if re.match(r"^services:\s*$", line):
            in_services = True
            continue
        if not in_services:
            continue
        if line and not line.startswith(" "):
            break
        service_match = re.match(r"^\s{2}([A-Za-z0-9_.-]+):\s*$", line)
        if service_match:
            current = service_match.group(1)
            services.setdefault(current, {"depends_on": [], "ports": [], "refs": []})
            section = None
            continue
        if not current:
            continue
        section_match = re.match(r"^\s{4}([A-Za-z0-9_-]+):", line)
        if section_match:
            section = section_match.group(1)
            inline = line.split(":", 1)[1]
            if section == "depends_on":
                services[current]["depends_on"].extend(_inline_list(inline))
            elif section == "ports":
                services[current]["ports"].extend(_inline_list(inline))
            elif section == "image" and inline.strip():
                services[current]["image"] = inline.strip().strip("'\"")
                services[current]["image_version"] = _image_version(str(services[current]["image"]))
            elif section == "build" and inline.strip():
                services[current]["build"] = inline.strip().strip("'\"")
            continue
        item_match = re.match(r"^\s{6}-\s*['\"]?([^'\"\s]+)", line)
        if item_match and section in {"depends_on", "ports"}:
            services[current][section].append(item_match.group(1))
        for ref in _extract_refs(line):
            services[current]["refs"].append(ref)
    return services


def _add_dockerfile(graph: Graph, root: Path, path: Path) -> None:
    rel = path.relative_to(root).as_posix()
    node_id = f"infra:image:{rel}"
    text = read_text(path)
    base = next((line.split(None, 1)[1] for line in text.splitlines() if line.upper().startswith("FROM ")), "")
    attrs = {"kind": "container_image", "path": rel}
    if base:
        attrs["base_image"] = base
        attrs["base_version"] = _image_version(base)
        attrs["deployment"] = "local"
    graph.add_node(node_id, path.name, attributes=attrs)
    graph.add_edge("infra-root", node_id, "builds")
    graph.add_node("infra:environment:local", "Local", attributes={"kind": "deployment_environment", "environment": "local"})
    graph.add_edge("infra:environment:local", node_id, "builds")


def _looks_like_kubernetes(path: Path) -> bool:
    if path.suffix not in {".yml", ".yaml"}:
        return False
    text = read_text(path, limit=200_000)
    return any(f"kind: {kind}" in text for kind in K8S_KINDS)


def _add_kubernetes(graph: Graph, root: Path, path: Path) -> None:
    rel = path.relative_to(root).as_posix()
    text = read_text(path)
    for doc in re.split(r"^---\s*$", text, flags=re.M):
        kind = _yaml_scalar(doc, "kind")
        name = _yaml_nested_scalar(doc, "metadata", "name")
        if not kind or not name or kind not in K8S_KINDS:
            continue
        node_id = f"infra:k8s:{kind.lower()}:{name}"
        attrs = {"kind": f"k8s_{kind.lower()}", "path": rel, "deployment": "cluster", "runtime": "kubernetes"}
        image = _yaml_nested_scalar(doc, "containers", "image") or _yaml_scalar(doc, "image")
        if image:
            attrs["image"] = image
            attrs["image_version"] = _image_version(image)
        graph.add_node(node_id, name, attributes=attrs)
        graph.add_edge("infra-root", node_id, "deploys")
        if kind in {"Service", "Ingress"}:
            app = _yaml_nested_scalar(doc, "selector", "app") or _yaml_nested_scalar(doc, "matchLabels", "app")
            if app:
                target_id = f"infra:k8s:deployment:{app}"
                graph.add_node(target_id, app, attributes={"kind": "k8s_deployment", "path": rel})
                graph.add_edge(node_id, target_id, "routes_to")


def _add_github_actions(graph: Graph, root: Path, path: Path) -> None:
    rel = path.relative_to(root).as_posix()
    text = read_text(path)
    pipeline_id = f"infra:pipeline:{rel}"
    graph.add_node(pipeline_id, path.stem, attributes={"kind": "ci_pipeline", "provider": "github_actions", "path": rel})
    graph.add_edge("infra-root", pipeline_id, "runs_pipeline")
    in_jobs = False
    for line in text.splitlines():
        if re.match(r"^jobs:\s*$", line):
            in_jobs = True
            continue
        if in_jobs:
            match = re.match(r"^\s{2}([A-Za-z0-9_.-]+):\s*$", line)
            if match:
                job = match.group(1)
                job_id = f"infra:ci_job:{rel}:{job}"
                graph.add_node(job_id, job, attributes={"kind": "ci_job", "path": rel})
                graph.add_edge(pipeline_id, job_id, "has_job")


def _add_dependencies(graph: Graph, root: Path, path: Path) -> None:
    rel = path.relative_to(root).as_posix()
    deps = _read_dependencies(path)
    if not deps:
        return
    group_id = f"infra:dependencies:{rel}"
    graph.add_node(group_id, path.name, attributes={"kind": "dependency_group", "path": rel})
    graph.add_edge("infra-root", group_id, "uses_dependencies")
    for manager, names in deps.items():
        for name in sorted(names):
            dep_id = f"infra:dependency:{manager}:{name}"
            graph.add_node(dep_id, name, attributes={"kind": "dependency", "manager": manager, "path": rel})
            graph.add_edge(group_id, dep_id, "depends_on")


def _read_dependencies(path: Path) -> dict[str, set[str]]:
    text = read_text(path)
    if path.name == "package.json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {}
        names: set[str] = set()
        for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            names.update((data.get(key) or {}).keys())
        return {"npm": names} if names else {}
    if path.name == "requirements.txt":
        names = {re.split(r"[<>=~!;\[]", line.strip(), 1)[0] for line in text.splitlines() if line.strip() and not line.startswith("#")}
        return {"pip": {name for name in names if name}}
    if path.name == "pyproject.toml":
        names = set(re.findall(r"['\"]([A-Za-z0-9_.-]+)(?:[<>=~!\[][^'\"]*)?['\"]", text))
        return {"python": names} if names else {}
    return {}


def _add_terraform(graph: Graph, root: Path, path: Path) -> None:
    rel = path.relative_to(root).as_posix()
    text = read_text(path)
    for provider in PROVIDER_RE.findall(text):
        provider_id = f"infra:cloud:{provider}"
        graph.add_node(provider_id, provider, attributes={"kind": "cloud_provider", "path": rel, "deployment": "cloud"})
        graph.add_edge("infra-root", provider_id, "uses_provider")
    for match in RESOURCE_BLOCK_RE.finditer(text):
        resource_type, name = match.group(1), match.group(2)
        provider, resource = _split_terraform_resource_type(resource_type)
        service = _cloud_service(provider, resource)
        resource_id = f"infra:cloud_resource:{provider}:{resource}:{name}"
        attrs = {
            "kind": "cloud_resource",
            "provider": provider,
            "cloud_service": service,
            "resource_type": resource_type,
            "resource_name": name,
            "path": rel,
            "deployment": "cloud",
        }
        attrs.update(_terraform_config(match.group("body")))
        graph.add_node(resource_id, name, attributes=attrs)
        graph.add_edge(f"infra:cloud:{provider}", resource_id, "provisions")
        service_id = f"infra:cloud_service:{provider}:{service}"
        graph.add_node(service_id, service, attributes={"kind": "cloud_service", "provider": provider, "path": rel})
        graph.add_edge(f"infra:cloud:{provider}", service_id, "offers")
        graph.add_edge(service_id, resource_id, "contains")


def _add_serverless(graph: Graph, root: Path, path: Path) -> None:
    rel = path.relative_to(root).as_posix()
    text = read_text(path)
    provider = _yaml_nested_scalar(text, "provider", "name") or _yaml_scalar(text, "provider")
    service = _yaml_scalar(text, "service") or path.parent.name
    service_id = f"infra:serverless:{service}"
    graph.add_node(service_id, service, attributes={"kind": "serverless_service", "path": rel})
    graph.add_edge("infra-root", service_id, "deploys")
    if provider:
        provider_id = f"infra:cloud:{provider}"
        graph.add_node(provider_id, provider, attributes={"kind": "cloud_provider", "path": rel, "deployment": "cloud"})
        graph.add_edge(service_id, provider_id, "runs_on")
        graph.add_node(service_id, service, attributes={"deployment": "cloud", "provider": provider})


def _add_integration_refs(graph: Graph, root: Path, path: Path) -> None:
    rel = path.relative_to(root).as_posix()
    refs = _extract_refs(read_text(path, limit=200_000))
    if not refs:
        return
    file_id = f"infra:integration_source:{rel}"
    graph.add_node(file_id, path.name, attributes={"kind": "integration_source", "path": rel})
    graph.add_edge("infra-root", file_id, "references")
    for ref in sorted(set(refs)):
        target = _target_id(ref)
        graph.add_node(target, ref, attributes={"kind": "integration", "path": rel})
        graph.add_edge(file_id, target, "integrates_with")


def _extract_refs(text: str) -> list[str]:
    refs = [match.group(1) for match in URL_REF_RE.finditer(text)]
    for match in ENV_REF_RE.finditer(text):
        value = match.group(2).strip()
        if value and not value.startswith("${"):
            refs.append(value)
    return refs


def _target_id(ref: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", ref).strip("-").lower()
    return f"infra:integration:{normalized or 'unknown'}"


def _cloud_service(provider: str, resource: str) -> str:
    if provider == "aws":
        first = resource.split("_", 1)[0]
        return AWS_SERVICE_ALIASES.get(first, first)
    if provider in {"google", "google-beta"}:
        return resource.split("_", 1)[0]
    if provider in {"azurerm", "azuread"}:
        return resource.split("_", 1)[0]
    return resource.split("_", 1)[0]


def _split_terraform_resource_type(resource_type: str) -> tuple[str, str]:
    known_providers = ("aws", "google-beta", "google", "azurerm", "azuread")
    for provider in known_providers:
        prefix = f"{provider}_"
        if resource_type.startswith(prefix):
            return provider, resource_type[len(prefix) :]
    provider, _, resource = resource_type.partition("_")
    return provider, resource or resource_type


def _terraform_config(body: str) -> dict[str, str]:
    config: dict[str, str] = {}
    for key, raw_value in ASSIGNMENT_RE.findall(body):
        if key not in CONFIG_KEYS:
            continue
        value = raw_value.strip().strip('"')
        if value and not value.startswith("{") and not value.startswith("["):
            config[f"config_{key}"] = value
    return config


def _image_version(image: str) -> str:
    if "@" in image:
        return image.split("@", 1)[1]
    last = image.rsplit("/", 1)[-1]
    if ":" in last:
        return last.rsplit(":", 1)[1]
    return "latest"


def _inline_list(value: str) -> list[str]:
    value = value.strip()
    if not value:
        return []
    if value.startswith("[") and value.endswith("]"):
        return [item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip()]
    return [value.strip().strip("'\"")]


def _yaml_scalar(text: str, key: str) -> str | None:
    match = re.search(rf"^\s*{re.escape(key)}:\s*['\"]?([^'\"\n#]+)", text, re.M)
    return match.group(1).strip() if match else None


def _yaml_nested_scalar(text: str, parent: str, key: str) -> str | None:
    parent_match = re.search(rf"^(\s*){re.escape(parent)}:\s*$", text, re.M)
    if not parent_match:
        return None
    indent = len(parent_match.group(1))
    start = parent_match.end()
    block_lines: list[str] = []
    for line in text[start:].splitlines():
        if line.strip() and len(line) - len(line.lstrip(" ")) <= indent:
            break
        block_lines.append(line)
    return _yaml_scalar("\n".join(block_lines), key)
