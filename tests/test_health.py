from fastapi import FastAPI
from fastapi.testclient import TestClient

from castor.routes.metrics import init_metrics_routes


def test_health_get_ok():
    app = FastAPI()
    init_metrics_routes(app)

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "OK"


def test_health_head_ok_without_body():
    app = FastAPI()
    init_metrics_routes(app)

    response = TestClient(app).head("/health")

    assert response.status_code == 200
    assert response.content == b""
