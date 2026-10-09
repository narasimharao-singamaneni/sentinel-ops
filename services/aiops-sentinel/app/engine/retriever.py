"""Local Markdown Runbook Retriever.

Reads `.md` runbooks from a local directory and matches query keywords
to the most relevant runbook file. Falls back to `default_fallback.md`
when no match is found.

Each runbook MAY declare YAML-style frontmatter between `---` fences,
e.g. `manual_triage_base_minutes: 90`, which is exposed via
`retrieve_with_meta` for the ROI model.
"""

import keyword
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "is", "was", "are", "after", "before", "if", "when", "while", "that",
    "this", "it", "its", "be", "by", "as", "at", "so", "do", "does",
}


@dataclass
class RunbookMatch:
    content: str
    filename: str
    metadata: dict = field(default_factory=dict)


def parse_runbook_metadata(text: str) -> dict:
    """Parse `key: value` frontmatter from the leading `---` fenced block.

    Values are kept as strings; integer-looking `*_minutes` values are
    converted to int. Files without frontmatter yield an empty dict.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    meta: dict = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if re.fullmatch(r"-?\d+", value):
                meta[key] = int(value)
            else:
                meta[key] = value
    return meta


class RunbookRetriever:
    """File-system retriever for markdown runbooks."""

    def __init__(self, runbook_dir: str | Path = "runbooks", fallback: str = "default_fallback.md"):
        self._runbook_dir = Path(runbook_dir)
        self._fallback_file = Path(fallback)

    def _words(self, query: str) -> set[str]:
        words = set(re.findall(r"[a-z0-9_]+", query.lower()))
        return {w for w in words if w and w not in _STOPWORDS and not keyword.iskeyword(w)}

    def _score(self, words: Iterable[str], md_file: Path) -> int:
        stem = md_file.stem.lower()
        stem_score = sum(1 for w in words if w in stem)
        content_score = 0
        try:
            content = md_file.read_text().lower()
        except OSError:
            content = ""
        content_score = sum(1 for w in words if w in content)
        # Stem matches are stronger signals than content matches.
        return stem_score * 10 + content_score

    def _match(self, query: str) -> list[str]:
        """Return runbook filenames ranked by match score (best first)."""
        words = self._words(query)

        if not words or not self._runbook_dir.exists():
            return []

        fallback_stem = Path(self._fallback_file).stem.lower()
        scored: list[tuple[int, str]] = []
        for md_file in self._runbook_dir.glob("*.md"):
            if md_file.stem.lower() == fallback_stem:
                continue
            score = self._score(words, md_file)
            if score > 0:
                scored.append((score, md_file.name))

        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return [name for _, name in scored]

    def retrieve(self, query: str) -> str:
        """Retrieve runbook markdown text matching the query.

        Returns the file content of the best match, or the fallback file content.
        Never raises FileNotFoundError.
        """
        result, _, _ = self.retrieve_with_meta(query)
        return result

    def retrieve_with_meta(self, query: str) -> tuple[str, str, dict]:
        """Retrieve runbook content, matched filename, and frontmatter metadata.

        Falls back to the fallback runbook (with its metadata) when nothing
        matches. Never raises FileNotFoundError.
        """
        matches = self._match(query)

        if matches:
            target = self._runbook_dir / matches[0]
            if target.exists():
                content = target.read_text()
                return content, target.name, parse_runbook_metadata(content)

        fallback_path = self._runbook_dir / self._fallback_file
        if fallback_path.exists():
            content = fallback_path.read_text()
            return content, fallback_path.name, parse_runbook_metadata(content)

        return (
            f"# No runbook found\n\nQuery: {query}\n\nNo matching runbook available. Manual review required.",
            "none",
            {},
        )

    def list_available(self) -> list[str]:
        """List all available runbook filenames (excluding fallback)."""
        if not self._runbook_dir.exists():
            return []

        return [
            f.name
            for f in sorted(self._runbook_dir.glob("*.md"))
            if f.name != self._fallback_file.name
        ]
