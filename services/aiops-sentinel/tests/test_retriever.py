from pathlib import Path

import pytest

from app.engine.retriever import RunbookRetriever


@pytest.fixture
def runbook_dir(tmp_path: Path) -> Path:
    """Create a temporary runbook directory with test files."""
    db_file = tmp_path / "db_latency.md"
    db_file.write_text("# Database Latency\nRemediate pool exhaustion.\n")

    fallback = tmp_path / "default_fallback.md"
    fallback.write_text("# Default Fallback\nManual review required.\n")

    cache_file = tmp_path / "cache_stale.md"
    cache_file.write_text("# Cache Staleness\nFlush cache entries.\n")

    return tmp_path


class TestRetrieverMatch:
    """Positive test: querying known keywords retrieves correct runbook context."""

    def test_query_db_latency_retrieves_db_runbook(self, runbook_dir: Path):
        retriever = RunbookRetriever(runbook_dir=runbook_dir, fallback="default_fallback.md")
        result = retriever.retrieve("database timeout")

        assert "Database Latency" in result
        assert "Remediate pool exhaustion" in result

    def test_query_cache_retrieves_cache_runbook(self, runbook_dir: Path):
        retriever = RunbookRetriever(runbook_dir=runbook_dir, fallback="default_fallback.md")
        result = retriever.retrieve("cache stale")

        assert "Cache Staleness" in result
        assert "Flush cache" in result


class TestRetrieverFallback:
    """Negative test: querying unknown error returns default_fallback.md without FileNotFoundError."""

    def test_unknown_query_returns_fallback(self, runbook_dir: Path):
        retriever = RunbookRetriever(runbook_dir=runbook_dir, fallback="default_fallback.md")
        result = retriever.retrieve("gibberish xyz unknown error")

        assert "Default Fallback" in result
        assert "Manual review" in result

    def test_no_fallback_file_returns_safe_message(self, runbook_dir: Path):
        """When fallback file doesn't exist, returns a safe message without raising."""
        retriever = RunbookRetriever(runbook_dir=runbook_dir, fallback="nonexistent.md")
        result = retriever.retrieve("gibberish")

        assert "No runbook found" in result
        assert "Manual review required" in result


class TestRetrieverEdgeCases:
    def test_empty_query_returns_fallback(self, runbook_dir: Path):
        retriever = RunbookRetriever(runbook_dir=runbook_dir, fallback="default_fallback.md")
        result = retriever.retrieve("")

        assert "Default Fallback" in result

    def test_list_available_excludes_fallback(self, runbook_dir: Path):
        retriever = RunbookRetriever(runbook_dir=runbook_dir, fallback="default_fallback.md")
        available = retriever.list_available()

        assert "db_latency.md" in available
        assert "cache_stale.md" in available
        assert "default_fallback.md" not in available

    def test_missing_runbook_dir_no_crash(self, tmp_path: Path):
        """When runbook_dir doesn't exist, doesn't crash — returns safe fallback."""
        nonexistent = tmp_path / "does_not_exist"
        retriever = RunbookRetriever(runbook_dir=nonexistent, fallback="default_fallback.md")
        result = retriever.retrieve("anything")

        assert "No runbook found" in result


class TestRetrieverMetadata:
    """Runbook frontmatter metadata (manual_triage_base_minutes) parsing + ranked real runbooks."""

    def test_parse_runbook_metadata_frontmatter(self):
        from app.engine.retriever import parse_runbook_metadata

        text = "---\nmanual_triage_base_minutes: 45\n---\n\n# Pool Exhaustion\n\nbody\n"
        meta = parse_runbook_metadata(text)
        assert meta == {"manual_triage_base_minutes": 45}

    def test_parse_runbook_metadata_absent(self):
        from app.engine.retriever import parse_runbook_metadata

        assert parse_runbook_metadata("# No frontmatter\n\nbody\n") == {}

    def test_retrieve_with_meta_returns_filename_and_metadata(self, runbook_dir):
        from app.engine.retriever import RunbookRetriever

        (runbook_dir / "db_latency.md").write_text(
            "---\nmanual_triage_base_minutes: 90\n---\n\n# Database Latency\nRemediate pool exhaustion.\n"
        )
        retriever = RunbookRetriever(runbook_dir=runbook_dir, fallback="default_fallback.md")
        content, filename, meta = retriever.retrieve_with_meta("database timeout")
        assert filename == "db_latency.md"
        assert meta["manual_triage_base_minutes"] == 90
        assert "Database Latency" in content


class TestRealRunbooks:
    """The shipped runbooks directory (project cwd) matches production-like events to the right class."""

    def test_503_rejection_snippet_matches_pool_exhaustion(self):
        from app.engine.retriever import RunbookRetriever

        retriever = RunbookRetriever()
        _, filename, meta = retriever.retrieve_with_meta(
            "503 Service Unavailable: Too many concurrent requests. Please try again shortly."
        )
        assert filename == "pool_exhaustion.md"
        assert meta.get("manual_triage_base_minutes") == 45

    def test_db_timeout_snippet_matches_db_latency(self):
        from app.engine.retriever import RunbookRetriever

        retriever = RunbookRetriever()
        _, filename, meta = retriever.retrieve_with_meta(
            "database query timeout after 1.0s (pg_sleep 3.0s exceeded DB_QUERY_TIMEOUT)"
        )
        assert filename == "db_latency.md"
        assert meta.get("manual_triage_base_minutes") == 90

    def test_unknown_snippet_falls_back(self):
        from app.engine.retriever import RunbookRetriever

        retriever = RunbookRetriever()
        _, filename, meta = retriever.retrieve_with_meta("zebra flapping in the server room")
        assert filename == "default_fallback.md"
        assert meta.get("manual_triage_base_minutes") == 180
