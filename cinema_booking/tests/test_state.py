import json

from cinema_booking.state import (
    add_ticket_request, get_item, list_ticket_requests,
    remove_ticket_request, update_item,
)


def test_add_then_list_round_trips(tmp_path):
    state_file = str(tmp_path / "state.json")
    item = add_ticket_request(
        provider="beta", movie_query="Người Nhện",
        date_range=["2026-08-12", "2026-08-12"],
        cinema_priority=["Beta Tây Sơn"], state_file=state_file,
    )
    assert item["status"] == "pending"
    assert item["quantity"] == 2
    assert item["prefer_sweetbox"] is False

    items = list_ticket_requests(state_file)
    assert len(items) == 1
    assert items[0]["id"] == item["id"]


def test_get_item_returns_none_when_missing(tmp_path):
    state_file = str(tmp_path / "state.json")
    add_ticket_request(provider="beta", movie_query="X", date_range=["2026-08-12", "2026-08-12"],
                        state_file=state_file)
    assert get_item("does-not-exist", state_file) is None


def test_update_item_merges_fields(tmp_path):
    state_file = str(tmp_path / "state.json")
    item = add_ticket_request(provider="beta", movie_query="X",
                               date_range=["2026-08-12", "2026-08-12"], state_file=state_file)
    updated = update_item(item["id"], path=state_file, status="pending_payment", hold_expiry="soon")
    assert updated["status"] == "pending_payment"
    assert updated["hold_expiry"] == "soon"
    # Persisted, not just returned:
    reloaded = get_item(item["id"], state_file)
    assert reloaded["status"] == "pending_payment"


def test_remove_ticket_request(tmp_path):
    state_file = str(tmp_path / "state.json")
    item = add_ticket_request(provider="beta", movie_query="X",
                               date_range=["2026-08-12", "2026-08-12"], state_file=state_file)
    assert remove_ticket_request(item["id"], state_file) is True
    assert list_ticket_requests(state_file) == []
    assert remove_ticket_request(item["id"], state_file) is False


def test_state_file_is_plain_json(tmp_path):
    state_file = str(tmp_path / "state.json")
    add_ticket_request(provider="beta", movie_query="X",
                        date_range=["2026-08-12", "2026-08-12"], state_file=state_file)
    with open(state_file, encoding="utf-8") as f:
        data = json.load(f)
    assert "items" in data
