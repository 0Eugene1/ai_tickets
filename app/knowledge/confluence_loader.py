from __future__ import annotations

import argparse
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Any, Iterable

from app.config import Settings, get_settings
from app.integrations.confluence import ConfluenceClient, ConfluenceConfig


class _StorageTextParser(HTMLParser):
    _blocks = {
        "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
        "blockquote", "pre", "table", "ul", "ol", "ac:task", "ac:rich-text-body",
    }
    _ignored = {"script", "style"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._ignored:
            self.ignored_depth += 1
            return
        if not self.ignored_depth and tag in self._blocks:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._ignored and self.ignored_depth:
            self.ignored_depth -= 1
            return
        if not self.ignored_depth and tag in self._blocks:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(data)


def storage_to_text(value: str) -> str:
    parser = _StorageTextParser()
    parser.feed(value)
    parser.close()
    text = unescape("".join(parser.parts)).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _page_record(page: dict[str, Any], base_url: str) -> dict[str, Any]:
    page_id = str(page.get("id", ""))
    version = _as_dict(page.get("version"))
    space = _as_dict(page.get("space"))
    links = _as_dict(page.get("_links"))
    body = _as_dict(_as_dict(page.get("body")).get("storage"))
    storage = str(body.get("value", ""))
    webui = str(links.get("webui", ""))
    source_url = f"{base_url.rstrip('/')}/{webui.lstrip('/')}" if webui else ""
    labels = _as_dict(_as_dict(page.get("metadata")).get("labels")).get("results", [])
    label_names = [
        str(item.get("name"))
        for item in labels
        if isinstance(item, dict) and item.get("name")
    ]
    ancestors = [
        str(item.get("id"))
        for item in page.get("ancestors", [])
        if isinstance(item, dict) and item.get("id")
    ]
    text = storage_to_text(storage)
    return {
        "id": f"confluence-{page_id}-v{version.get('number', 0)}",
        "source_page_id": page_id,
        "title": str(page.get("title", "")),
        "text": text,
        "space_key": str(space.get("key", "")),
        "space_name": str(space.get("name", "")),
        "ancestor_ids": ancestors,
        "labels": label_names,
        "version": version.get("number"),
        "updated_at": version.get("when"),
        "source": f"confluence:{page_id}",
        "source_url": source_url,
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "confirmed": False,
        "semantic_status": "unverified_document",
        "automatic_answer_allowed": False,
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _jsonl_line(value: Any) -> str:
    # U+2028/U+2029 are valid inside JSON strings but str.splitlines() treats them
    # as record separators. Escaping keeps the JSONL safe for common readers.
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
        + "\n"
    )


def _existing_versions(path: Path) -> set[tuple[str, str]]:
    versions: set[tuple[str, str]] = set()
    if not path.exists():
        return versions
    for line in path.read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        item = json.loads(line)
        versions.add((str(item.get("source_page_id", "")), str(item.get("version", ""))))
    return versions


def _selection_id(space_keys: Iterable[str]) -> str:
    normalized = "\n".join(sorted({key.strip() for key in space_keys if key.strip()}))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def export_pages(
    client: ConfluenceClient,
    *,
    output_dir: Path,
    space_keys: list[str],
    limit: int = 50,
    resume: bool = True,
) -> dict[str, Any]:
    keys = sorted({key.strip() for key in space_keys if key.strip()})
    if not keys:
        raise ValueError("Select at least one Confluence space")
    output_dir.mkdir(parents=True, exist_ok=True)
    pages_path = output_dir / "pages.jsonl"
    progress_path = output_dir / "progress.json"
    selection_id = _selection_id(keys)
    previous = _read_json(progress_path) if resume else {}
    if previous.get("status") != "in_progress" or previous.get("selection_id") != selection_id:
        previous = {}

    completed = {str(value) for value in previous.get("completed_space_keys", [])}
    current_space = str(previous.get("current_space_key", ""))
    request_url = str(previous.get("next_request_url", "")) or None
    known = _existing_versions(pages_path)
    written = int(previous.get("pages_written", 0))
    skipped = int(previous.get("pages_skipped", 0))

    progress: dict[str, Any] = {
        "status": "in_progress",
        "selection_id": selection_id,
        "space_keys": keys,
        "completed_space_keys": sorted(completed),
        "current_space_key": current_space or None,
        "next_request_url": request_url,
        "pages_written": written,
        "pages_skipped": skipped,
    }
    _write_json(progress_path, progress)

    try:
        with pages_path.open("a", encoding="utf-8") as output:
            for key in keys:
                if key in completed:
                    continue
                next_url = request_url if current_space == key else None
                while True:
                    progress["current_space_key"] = key
                    progress["next_request_url"] = next_url
                    _write_json(progress_path, progress)
                    batch = client.page_batch(space_key=key, limit=limit, request_url=next_url)
                    for page in batch.results:
                        record = _page_record(page, client.base_url)
                        identity = (str(record["source_page_id"]), str(record["version"]))
                        if identity in known:
                            skipped += 1
                            continue
                        output.write(_jsonl_line(record))
                        output.flush()
                        known.add(identity)
                        written += 1
                    next_url = batch.next_url
                    progress["pages_written"] = written
                    progress["pages_skipped"] = skipped
                    progress["next_request_url"] = next_url
                    _write_json(progress_path, progress)
                    if not next_url:
                        break
                completed.add(key)
                current_space = ""
                request_url = None
                progress["completed_space_keys"] = sorted(completed)
                progress["current_space_key"] = None
                progress["next_request_url"] = None
                _write_json(progress_path, progress)
    except Exception as exc:
        progress["last_error"] = type(exc).__name__
        _write_json(progress_path, progress)
        raise

    progress["status"] = "completed"
    progress["completed_space_keys"] = sorted(completed)
    progress["current_space_key"] = None
    progress["next_request_url"] = None
    progress.pop("last_error", None)
    _write_json(progress_path, progress)
    return progress


def _client_from_settings(settings: Settings) -> ConfluenceClient:
    if not settings.confluence_base_url:
        raise ValueError("CONFLUENCE_BASE_URL is required")
    if not settings.confluence_api_token:
        raise ValueError("CONFLUENCE_API_TOKEN is required")
    return ConfluenceClient(
        ConfluenceConfig(
            base_url=settings.confluence_base_url,
            auth_mode=settings.confluence_auth_mode,
            email=settings.confluence_email,
            api_token=settings.confluence_api_token,
            timeout_seconds=settings.confluence_timeout_seconds,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Confluence page exporter")
    parser.add_argument("--output", type=Path, default=Path("data/confluence"))
    parser.add_argument("--space-key", action="append", default=[])
    parser.add_argument("--list-spaces", action="store_true")
    parser.add_argument("--restart", action="store_true", help="Ignore an unfinished checkpoint")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    settings = get_settings()
    configured_keys = [key for key in settings.confluence_space_keys.split(",") if key.strip()]

    with _client_from_settings(settings) as client:
        if args.list_spaces:
            spaces = [
                {
                    "id": str(item.get("id", "")),
                    "key": str(item.get("key", "")),
                    "name": str(item.get("name", "")),
                    "type": str(item.get("type", "")),
                    "status": str(item.get("status", "")),
                }
                for item in client.iter_spaces(limit=args.limit)
            ]
            args.output.mkdir(parents=True, exist_ok=True)
            _write_json(args.output / "spaces.json", spaces)
            print(f"Saved {len(spaces)} accessible spaces to {args.output / 'spaces.json'}")
            return

        result = export_pages(
            client,
            output_dir=args.output,
            space_keys=args.space_key or configured_keys,
            limit=args.limit,
            resume=not args.restart,
        )
        print(
            f"Confluence export completed: {result['pages_written']} new page versions, "
            f"{result['pages_skipped']} already present"
        )


if __name__ == "__main__":
    main()
