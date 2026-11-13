import re
from pathlib import Path
from typing import Any, Dict

import click
import yaml


class WorkflowScanner:
    def __init__(self):
        # Regex to catch: secrets.NAME, vars.NAME
        self.pattern = re.compile(r"\b(secrets|vars)\.([a-zA-Z0-9_-]+)\b")
        self.results = {}
        self.global_usage = {"secrets": set(), "vars": set()}

    def _extract_from_string(self, text: str, file_refs: Dict[str, set]) -> None:
        matches = self.pattern.findall(text)
        for context, name in matches:
            file_refs[context].add(name)

    def _walk_node(self, node: Any, file_refs: Dict[str, set]) -> None:
        if isinstance(node, dict):
            for value in node.values():
                self._walk_node(value, file_refs)
        elif isinstance(node, list):
            for item in node:
                self._walk_node(item, file_refs)
        elif isinstance(node, str):
            self._extract_from_string(node, file_refs)

    def scan_file(self, file_path: Path) -> None:
        try:
            with file_path.open(encoding="utf-8") as file:
                data = yaml.safe_load(file)
            if not data:
                return

            file_refs = {"secrets": set(), "vars": set()}
            self._walk_node(data, file_refs)
            if any(file_refs.values()):
                self.results[file_path.name] = file_refs
                for context, names in file_refs.items():
                    self.global_usage[context].update(names)
        except Exception:
            pass

    def scan_path(self, path: Path) -> None:
        files = []
        if path.is_file():
            files = [path]
        elif path.is_dir():
            files = list(path.glob("**/*.yml")) + list(path.glob("**/*.yaml"))

        with click.progressbar(files, label="Scanning Workflow Files") as bar:
            for f in bar:
                self.scan_file(f)
