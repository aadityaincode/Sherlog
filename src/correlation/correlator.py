"""
Source code correlator: link the investigation engine's failure_point to the
actual line of code in the demo-app repo.

Takes the engine's output (e.g. failure_point "SubscriptionService.renew",
error "DatabaseConnectionError") and returns {file, line, code_snippet} by
cloning the repo with GitPython and walking it: find the class, find the
method, and inside the method find the exception handler — for Scenario C
that's the `except Exception: pass` that swallows the error.

Deterministic on purpose: no LLM here, so a correct investigation always
correlates to the same file:line and can't hallucinate a location.
"""
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from git import Repo

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

REPO_URL = os.environ.get("DEMO_APP_REPO_URL", "https://github.com/aadityaincode/novastream-billing")
# Gitignored working clone; reused across runs, pulled to stay current.
CLONE_DIR = Path(__file__).resolve().parents[2] / ".repo_cache" / "novastream-billing"

SNIPPET_CONTEXT_LINES = 5


def get_repo() -> Repo:
    """Clone the demo-app repo on first use, pull on subsequent uses."""
    if CLONE_DIR.exists():
        repo = Repo(CLONE_DIR)
        repo.remotes.origin.pull()
        return repo
    CLONE_DIR.parent.mkdir(parents=True, exist_ok=True)
    return Repo.clone_from(REPO_URL, CLONE_DIR)


def _find_class_file(class_name: str) -> Path | None:
    pattern = re.compile(rf"^\s*class\s+{re.escape(class_name)}\b")
    for path in sorted(CLONE_DIR.rglob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if pattern.match(line):
                return path
    return None


def _method_span(lines: list[str], method_name: str) -> tuple[int, int] | None:
    """1-indexed [start, end] of the method body, found by indentation."""
    def_re = re.compile(rf"^(\s*)def\s+{re.escape(method_name)}\b")
    start = indent = None
    for i, line in enumerate(lines, 1):
        m = def_re.match(line)
        if m:
            start, indent = i, len(m.group(1))
            break
    if start is None:
        return None
    for j in range(start + 1, len(lines) + 1):
        line = lines[j - 1]
        if line.strip() and (len(line) - len(line.lstrip())) <= indent:
            return start, j - 1
    return start, len(lines)


def correlate(failure_point: str) -> dict | None:
    """Resolve 'ClassName.method' to {file, line, code_snippet}.

    The returned line is the exception handler inside the method if there is
    one (the swallowed-exception line for Scenario C), else the method's def
    line. Returns None if the class or method can't be found.
    """
    get_repo()

    if "." not in failure_point:
        return None
    class_name, method_name = failure_point.rsplit(".", 1)

    path = _find_class_file(class_name)
    if path is None:
        return None

    lines = path.read_text(encoding="utf-8").splitlines()
    span = _method_span(lines, method_name)
    if span is None:
        return None
    start, end = span

    line_no = next(
        (i for i in range(start, end + 1) if re.match(r"^\s*except\b", lines[i - 1])),
        start,
    )

    lo = max(1, line_no - SNIPPET_CONTEXT_LINES)
    hi = min(len(lines), line_no + SNIPPET_CONTEXT_LINES)
    snippet = "\n".join(f"{i:4d}  {lines[i - 1]}" for i in range(lo, hi + 1))

    return {
        "file": str(path.relative_to(CLONE_DIR)),
        "line": line_no,
        "code_snippet": snippet,
    }


if __name__ == "__main__":
    # Smoke test against the answer key's expected location:
    # app/services/subscription_service.py line 38.
    import json

    result = correlate("SubscriptionService.renew")
    print(json.dumps(result, indent=2))
