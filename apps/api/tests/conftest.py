import os
import tempfile
from pathlib import Path

test_database_path = Path(tempfile.gettempdir()) / f"atlas-test-research-{os.getpid()}.db"
test_token_path = Path(tempfile.gettempdir()) / f"atlas-test-token-{os.getpid()}"
os.environ["DATABASE_URL"] = f"sqlite:///{test_database_path.as_posix()}"
os.environ["LOCAL_TOKEN_FILE"] = str(test_token_path)
os.environ["ENABLE_EMBEDDINGS"] = "false"
os.environ["AZURE_OPENAI_ENDPOINT"] = ""
os.environ["AZURE_OPENAI_API_KEY"] = ""

test_token_path.write_text("test-local-token-which-is-long-enough", encoding="utf-8")

import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def client():
    with TestClient(app) as value:
        yield value


@pytest.fixture
def auth_headers():
    return {"x-local-token": "test-local-token-which-is-long-enough"}


def pytest_sessionfinish(session, exitstatus):
    del session, exitstatus
    for path in (
        test_database_path,
        Path(f"{test_database_path}-wal"),
        Path(f"{test_database_path}-shm"),
        test_token_path,
    ):
        path.unlink(missing_ok=True)
