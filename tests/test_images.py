"""Classifier, ground-side preprocessing and the /images endpoints."""

import base64
import io
import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import classifier
import vision
from app import ALERTS, CLASSIFIED, app

FIXTURES = Path(__file__).resolve().parent / "fixtures"
client = TestClient(app)

needs_model = pytest.mark.skipif(not classifier.available(), reason="OpenVINO runtime/model not available")


def _synthetic_frame(beetle_on_leaf: bool = True, size=(960, 540)) -> bytes:
    """Green foliage (or bare soil) with one small saturated orange-brown blob."""
    w, h = size
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[...] = (60, 140, 50) if beetle_on_leaf else (150, 128, 100)
    arr[200:216, 400:418] = (196, 120, 50)  # hue ~26 deg, s ~0.74, v ~0.77
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


# --- classifier -------------------------------------------------------------


def test_preprocess_matches_training_transform():
    x = classifier.preprocess(Image.new("RGB", (1920, 1080), (128, 128, 128)))
    assert x.shape == (3, 224, 224)
    assert x.dtype == np.float32


@needs_model
def test_classifier_on_field_crops():
    beetle, leaf = classifier.classify(
        [Image.open(FIXTURES / "cpb_adult_crop.jpg"), Image.open(FIXTURES / "leaf_crop.jpg")]
    )
    assert beetle["label"] == "cpb" and beetle["p_cpb"] > 0.9
    assert leaf["label"] == "not_cpb" and leaf["p_cpb"] < 0.1


@needs_model
def test_model_info_reports_shipped_artefact():
    info = classifier.info()
    assert info["loaded"] is True
    assert 6.0 < info["size_mb"] < 7.0  # KPI-7: 6.12 MB INT8 IR (+ xml)
    assert info["heldout_accuracy"] == pytest.approx(0.9621)


# --- ground-side preprocessing ----------------------------------------------


def test_candidate_found_on_foliage():
    img = Image.open(io.BytesIO(_synthetic_frame(True)))
    cands = vision.find_candidates(img)
    assert len(cands) == 1
    assert abs(cands[0].cx - 409) < 24 and abs(cands[0].cy - 208) < 24


def test_no_candidate_on_bare_soil():
    """Leaf-context rule: an orange speck on soil is not sent."""
    img = Image.open(io.BytesIO(_synthetic_frame(False)))
    assert vision.find_candidates(img) == []


def test_package_accounting():
    data = _synthetic_frame(True)
    pkg = vision.build_package(data, "synthetic.png", {"lat": 40.33, "lon": 15.61})
    k = pkg.kpi6()
    assert k["raw_bytes"] == len(data)
    assert k["p0_summary"] < 1024  # the P0 alert is sub-kilobyte
    assert k["sent_essential_bytes"] == k["p0_summary"] + k["p1_crops"]
    assert k["sent_total_bytes"] == k["sent_essential_bytes"] + k["p2_preview"]
    doc = pkg.crops_doc()
    assert len(doc["crops"]) == 1
    crop = vision.decode_crop(doc["crops"][0]["jpeg_b64"])
    assert crop.size == (224, 224)


# --- API ----------------------------------------------------------------------


@needs_model
def test_images_whole_frame():
    r = client.post("/images?frame=t1.png&lat=40.33&lon=15.61", content=_synthetic_frame(True),
                    headers={"Content-Type": "image/png"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["candidates"] == 1 and body["crops"] == 1
    assert "kpi6" in body and body["kpi6"]["raw_bytes"] > 0


def test_images_rejects_empty_and_garbage():
    assert client.post("/images", content=b"").status_code == 422
    if classifier.available():
        assert client.post("/images", content=b"not an image").status_code == 422


@needs_model
def test_gateway_flow_summary_then_package():
    """P0 then P1 for one frame -> one alert that ends 'classified'."""
    frame = "flow.png"
    s = client.post("/images/summary", json={"frame": frame, "n_candidates": 1,
                                             "candidates": [[100, 100]], "t_gateway_in": 1.0,
                                             "lat": 40.33, "lon": 15.61})
    assert s.status_code == 202
    b64 = base64.b64encode((FIXTURES / "cpb_adult_crop.jpg").read_bytes()).decode()
    p = client.post("/images/package", json={"frame": frame, "lat": 40.33, "lon": 15.61,
                                             "crops": [{"box": [0, 0, 224, 224], "jpeg_b64": b64}]})
    assert p.status_code == 200, p.text
    body = p.json()
    assert len(body["detections"]) == 1
    assert body["detections"][0]["confidence"] > 0.9
    alert = next(a for a in client.get("/alerts").json()["alerts"] if a["frame"] == frame)
    assert alert["state"] == "classified" and alert["cpb"] == 1
    assert alert["t_summary_received"] <= alert["t_classified"]
    # memory /detections now serves classifier output, not the June mock
    det = client.get("/detections").json()
    assert det["source"] == "memory"
    assert any(d["frame"] == frame for d in det["detections"])


def test_package_rejects_bad_jpeg():
    if not classifier.available():
        pytest.skip("model not available")
    r = client.post("/images/package", json={"frame": "x", "crops": [{"box": [0, 0, 1, 1], "jpeg_b64": "AAAA"}]})
    assert r.status_code == 422


def test_model_endpoint():
    r = client.get("/model")
    assert r.status_code == 200
    body = r.json()
    assert body["classes"] == ["cpb", "not_cpb"]
    assert body["roi"]["crop_px"] == 224


def test_alerts_limit_bounds():
    assert client.get("/alerts?limit=0").status_code == 200
    assert client.get("/alerts?limit=100000").status_code == 200


@pytest.fixture(autouse=True)
def _clean_memory():
    yield
    CLASSIFIED.clear()
    ALERTS.clear()
