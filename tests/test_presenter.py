"""Presenter state: POST /admin/presenter sets fields, null clears one, DELETE clears all."""

import httpx

ADMIN = {"X-Admin-Token": "dev-admin-token"}


def post(stack, body, headers=ADMIN):
    return httpx.post(stack.breaker_url + "/admin/presenter", json=body, headers=headers, timeout=5)


def test_presenter_set_partial_update_and_clear(stack):
    assert stack.get("/state")["presenter"] == {
        "caption": None, "spotlight": None, "card": None, "step": None, "step_done": [],
        "panel": None, "active": False}

    r = post(stack, {"caption": "Case 2: unusual, but legitimate.", "spotlight": "layer1",
                     "card": {"kicker": "Case 2", "title": "Soft stop"}, "step": 2,
                     "step_done": [0, 1], "panel": {"title": "Replay", "text": "a\nb"}})
    assert r.status_code == 200, r.text
    p = stack.get("/state")["presenter"]
    assert p == {"caption": "Case 2: unusual, but legitimate.", "spotlight": "layer1",
                 "card": {"kicker": "Case 2", "title": "Soft stop"}, "step": 2,
                 "step_done": [0, 1], "panel": {"title": "Replay", "text": "a\nb"}, "active": True}

    # A missing field keeps its value, null clears it.
    assert post(stack, {"spotlight": "layer2", "card": None, "panel": None}).status_code == 200
    p = stack.get("/state")["presenter"]
    assert p["spotlight"] == "layer2" and p["card"] is None and p["panel"] is None
    assert p["caption"] == "Case 2: unusual, but legitimate." and p["step"] == 2
    assert p["step_done"] == [0, 1] and p["active"] is True

    r = httpx.delete(stack.breaker_url + "/admin/presenter", headers=ADMIN, timeout=5)
    assert r.status_code == 200 and r.json()["active"] is False
    assert stack.get("/state")["presenter"]["active"] is False
    assert stack.get("/state")["presenter"]["caption"] is None


def test_presenter_needs_admin_token(stack):
    assert post(stack, {"caption": "x"}, headers={}).status_code == 401
    assert post(stack, {"caption": "x"}, headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert httpx.delete(stack.breaker_url + "/admin/presenter", timeout=5).status_code == 401
    assert stack.get("/state")["presenter"]["caption"] is None


def test_presenter_rejects_bad_values(stack):
    for body in ({"spotlight": "nowhere"}, {"step": 6}, {"step_done": [9]},
                 {"unknown": 1}, {"card": {"kicker": "x"}}, {"caption": "x" * 401}):
        assert post(stack, body).status_code == 422, body
    assert stack.get("/state")["presenter"]["active"] is False
