"""Check the shareable file set without inspecting ignored local data."""
from __future__ import annotations

import json
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"

class Page(HTMLParser):
    def __init__(self, source: Path):
        super().__init__()
        self.base = "/" + source.relative_to(FRONTEND).as_posix()
        self.refs: list[str] = []
    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "base" and values.get("href"):
            self.base = urljoin(self.base, values["href"])
        if tag in {"script", "img", "iframe", "source", "audio", "video"} and values.get("src"):
            self.refs.append(values["src"])
        if tag == "link" and values.get("href"):
            self.refs.append(values["href"])
        if values.get("poster"):
            self.refs.append(values["poster"])

def main() -> int:
    result = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT, check=True, capture_output=True)
    files = {p.decode("utf-8") for p in result.stdout.split(b"\0") if p}
    errors = []
    references = 0
    def check_ref(source: Path, base: str, ref: str):
        nonlocal references
        parsed = urlsplit(ref)
        if parsed.scheme or parsed.netloc or not parsed.path:
            return
        references += 1
        target = FRONTEND / unquote(urlsplit(urljoin(base, ref)).path).lstrip("/")
        relative = target.resolve().relative_to(ROOT).as_posix()
        if relative not in files or not target.is_file():
            errors.append(f"{source.relative_to(ROOT)}: missing shared asset {relative}")
    for name in sorted(files):
        path = ROOT / name
        if not path.is_file():
            errors.append(f"Not a regular shared file: {name}")
            continue
        if path.name == ".env" or re.search(r"\.(db|sqlite3?|log)(-wal|-shm)?$", name):
            errors.append(f"Local data must not be shared: {name}")
        if path.suffix in {".js", ".cjs", ".html", ".css", ".py", ".ps1", ".json"}:
            text = path.read_text(encoding="utf-8-sig")
            if re.search(r"\b[A-Za-z]:[\\/]", text):
                errors.append(f"Machine-specific absolute path: {name}")
        if name.startswith("frontend/") and path.suffix == ".html":
            page = Page(path); page.feed(text)
            for ref in page.refs: check_ref(path, page.base, ref)
        if name.startswith("frontend/") and path.suffix == ".css":
            base = "/" + path.relative_to(FRONTEND).as_posix()
            for match in re.finditer(r"url\(\s*['\"]?([^'\"\s)]+)", text): check_ref(path, base, match[1])
    source = (FRONTEND / "ui/roles.js").read_text(encoding="utf-8")
    roles = json.loads(source.removeprefix("window.DESIGN_DATA = ").strip().removesuffix(";"))
    for role in roles["people"]:
        for key in ("portrait", "personalityCard"): check_ref(FRONTEND / "ui/roles.js", "/ui/roles.js", role[key])
    for error in errors: print(error)
    print(f"Checked {len(files)} shared files and {references} asset references; {len(errors)} errors.")
    return bool(errors)

if __name__ == "__main__": raise SystemExit(main())
