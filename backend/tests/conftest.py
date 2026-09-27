"""Test-session setup. Env vars here MUST be set before any `app.*` module is
imported anywhere in the test run (pytest always loads this conftest first for
its directory tree), since app.config.Settings reads them once at import time
and several modules construct clients/DB engines at import time too."""

import os
import tempfile

_tmp_dir = tempfile.mkdtemp(prefix="euro_food_finder_test_")
os.environ["OPENAI_API_KEY"] = "sk-test-dummy-key-not-real"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_dir}/test.db"
os.environ["CHROMA_PERSIST_DIR"] = f"{_tmp_dir}/chroma"

import pytest  # noqa: E402

from app.db.session import get_session, init_db  # noqa: E402

init_db()


@pytest.fixture
def db_session():
    with get_session() as session:
        yield session
