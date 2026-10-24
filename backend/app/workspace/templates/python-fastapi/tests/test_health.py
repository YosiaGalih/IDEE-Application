from fastapi.testclient import TestClient

from service.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_item_validation() -> None:
    assert client.get("/items/3").json() == {"item_id": 3}
    assert client.get("/items/abc").status_code == 422
