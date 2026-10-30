"""Test configuration: every test run gets its own empty SENTINEL_HOME and the offline LLM provider,
so tests are fast, deterministic, free, and never touch your real var/ directory."""

import os
import tempfile

os.environ["SENTINEL_HOME"] = tempfile.mkdtemp(prefix="sentinel-test-")
os.environ["LLM_PROVIDER"] = "offline"
os.environ.setdefault("SENTINEL_N_CUSTOMERS", "120")
os.environ.setdefault("SENTINEL_N_DAYS", "30")
