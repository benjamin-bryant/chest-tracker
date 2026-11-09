import pytest
import requests

from fortnite_stats import API_URL, FortniteStatsError, fetch_stats


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.payload = payload

    def json(self):
        return self.payload


def test_fetch_stats_sends_account_and_window_and_normalizes(monkeypatch):
    calls = []
    payload = {
        "data": {
            "account": {"name": "Player One", "id": "account-id"},
            "stats": {
                "all": {
                    "overall": {"wins": 10, "winRate": 12.5, "kd": 2.0, "kills": 100},
                    "solo": {"wins": 3, "winRate": 8.0, "kd": 1.5, "kills": 40},
                    "duo": None,
                    "squad": {"wins": 7, "winRate": 15.0, "kd": 2.5, "kills": 60},
                }
            },
        }
    }

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(200, payload)

    monkeypatch.setattr(requests, "get", fake_get)
    result = fetch_stats(" test-key ", " Player One ", "epic", "season")

    assert calls[0][0] == API_URL
    assert calls[0][1]["headers"] == {"Authorization": "test-key"}
    assert calls[0][1]["params"] == {
        "name": "Player One", "accountType": "epic", "timeWindow": "season",
    }
    assert result["account"] == {"name": "Player One", "id": "account-id"}
    assert result["stats"]["overall"]["wins"] == 10
    assert result["stats"]["duo"] is None
    assert "minutesPlayed" not in result["stats"]["overall"]


@pytest.mark.parametrize("status, message", [
    (401, "API key was rejected"),
    (403, "API denied"),
    (404, "No stats were found"),
    (429, "rate-limiting"),
])
def test_fetch_stats_reports_common_http_errors(monkeypatch, status, message):
    monkeypatch.setattr(requests, "get", lambda *_args, **_kwargs: FakeResponse(status, {}))
    with pytest.raises(FortniteStatsError, match=message):
        fetch_stats("key", "Player")


def test_fetch_stats_rejects_missing_data(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *_args, **_kwargs: FakeResponse(200, {"data": {}}))
    with pytest.raises(FortniteStatsError, match="did not return stats"):
        fetch_stats("key", "Player")


def test_fetch_stats_validates_inputs_before_request(monkeypatch):
    called = False

    def unexpected_request(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(requests, "get", unexpected_request)
    with pytest.raises(FortniteStatsError, match="Choose Epic"):
        fetch_stats("key", "Player", "steam")
    assert not called
