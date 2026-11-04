import pytest
from app import store


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PROVIDER", "local")
    monkeypatch.setenv("EMAIL_ENABLED","false")
    monkeypatch.setenv("EMAIL_NOTIFY_PUBLICATION","false")
    for key in ('LLM_PRIMARY_ROUTE','LLM_FALLBACK_ROUTES','LLM_ALLOW_CROSS_PROVIDER_FALLBACK'):
        monkeypatch.delenv(key,raising=False)
    monkeypatch.delenv("APP_API_TOKEN", raising=False)
    monkeypatch.delenv("API_ALLOWED_HOSTS", raising=False)
    store.init()
