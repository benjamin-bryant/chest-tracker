import json
import sqlite3
import sys
import threading
import time
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import ingest
from detection import (
    CANCELLED, OPENED, PENDING, ChestDetector, PromptMatcher, scene_similarity, scene_thumb,
)
from protocol import format_delta, is_webhook_url, name_problem, parse_delta
from store import Store
from settings import Settings, Stats, import_old_setup
from watcher import Sender, WatchEngine, best_monitor, check_webhook, key_code, selection_too_big
from widgets import MatchMeter
import watcher


def play(frames, step=0.08):
    """Run (prompt, key) or (prompt, key, looked_away) frames and count finished chests."""
    detector = ChestDetector()
    events = [detector.update(100 + i * step, *frame) for i, frame in enumerate(frames)]
    return events.count(OPENED)


GONE = [(False, False)] * 45  # 3.6 seconds at the test's frame rate
PROMPT = [(True, False)] * 5


def test_opening_a_chest_counts_once():
    assert play(PROMPT + [(True, True)] * 2 + GONE) == 1


def test_press_on_the_frame_the_prompt_vanishes_counts():
    assert play(PROMPT + [(False, True)] + GONE) == 1


def test_walking_past_a_chest_does_not_count():
    assert play(PROMPT * 2 + GONE) == 0


def test_pressing_interact_without_a_prompt_does_not_count():
    assert play([(False, True)] * 10 + GONE) == 0


def test_cancel_with_prompt_coming_back_does_not_count():
    assert play(PROMPT + [(True, True)] + [(False, False)] * 15 + PROMPT + GONE) == 0


def test_interact_and_cancel_over_and_over_never_counts():
    attempt = PROMPT + [(True, True)] + [(False, False)] * 20
    assert play(attempt * 10 + PROMPT) == 0


def test_looking_away_during_the_search_does_not_count():
    looked_away = [(False, False, True)] * 5
    assert play(PROMPT + [(True, True)] + looked_away + GONE) == 0


def test_interact_look_away_repeated_with_long_waits_never_counts():
    attempt = PROMPT + [(True, True)] + [(False, False, True)] * 5 + GONE
    assert play(attempt * 5) == 0


def test_looking_away_after_the_search_still_counts():
    late_turn = [(False, False)] * 15 + [(False, False, True)] * 30
    assert play(PROMPT + [(True, True)] + late_turn + GONE) == 1


def test_prompt_flicker_does_not_cancel_a_real_open():
    flicker = [(False, False), (True, False)]
    assert play(PROMPT + [(True, True)] + flicker + GONE) == 1


def test_pressing_and_staying_on_the_prompt_is_cancelled_not_counted():
    detector = ChestDetector()
    events = [detector.update(100 + i * 0.1, True, i == 3) for i in range(30)]
    assert events.count(CANCELLED) == 1
    assert OPENED not in events


def test_search_reports_pending_then_opened():
    detector = ChestDetector()
    frames = PROMPT + [(True, True)] + GONE
    events = [detector.update(100 + i * 0.08, *f) for i, f in enumerate(frames)]
    assert [e for e in events if e] == [PENDING, OPENED]


def test_moving_on_to_the_next_chest_counts_the_finished_one():
    """Finish a search, turn to another chest within the confirm window: the first still counts."""
    turned = [(True, False, True)] * 3  # the next prompt shows up after the view has changed
    first = PROMPT + [(True, True)] + [(False, False)] * 16 + turned
    assert play(first) == 1


def test_back_to_back_chests_both_count():
    first = PROMPT + [(True, True)] + [(False, False)] * 16
    second = [(True, False, True)] * 3 + [(True, True)] + GONE
    assert play(first + second) == 2


def test_prompt_returning_to_the_same_view_still_cancels():
    cancel = PROMPT + [(True, True)] + [(False, False)] * 16 + [(True, False, False)] * 3 + GONE
    assert play(cancel) == 0


def test_a_view_change_in_the_first_second_still_cancels_when_the_prompt_returns():
    # Looked away while holding the key (within AIM_SECONDS): this was not a clean search.
    cancel = PROMPT + [(True, True)] + [(False, False, True)] * 4 + [(True, False, True)] * 3 + GONE
    assert play(cancel) == 0


def test_a_quick_cancel_never_counts_even_if_the_view_has_moved():
    # The prompt is back within a second of pressing: too soon to have been a finished search.
    quick = PROMPT + [(True, True)] + [(False, False)] * 6 + [(True, False, True)] * 3 + GONE
    assert play(quick) == 0


def test_cancelling_over_and_over_while_turning_never_counts():
    attempt = PROMPT + [(True, True)] + [(False, False)] * 6 + [(True, False, True)] * 3
    assert play(attempt * 8) == 0


def test_two_chests_in_a_row_count_twice():
    one = PROMPT + [(True, True)] + GONE
    assert play(one + one) == 2


def test_losing_focus_clears_a_half_finished_open():
    detector = ChestDetector()
    detector.update(100.0, True, True)
    detector.reset()
    assert detector.update(105.0, False, False) is None


def textured_view(seed=7, size=(400, 1400)):
    """A wide scene: sky above, ground below, soft shapes throughout."""
    rng = np.random.default_rng(seed)
    noise = cv2.GaussianBlur(rng.normal(0, 90, size).astype(np.float32), (0, 0), 14) * 6
    rows = np.linspace(190, 70, size[0], dtype=np.float32)[:, None]
    return np.clip(rows + noise, 0, 255).astype(np.uint8)


def test_scene_similarity_tells_staying_from_turning():
    world = textured_view()
    view = lambda x: world[:, x:x + 700]
    still = scene_thumb(view(100))
    assert scene_similarity(still, scene_thumb(view(100))) > 0.99
    assert scene_similarity(still, scene_thumb(view(112))) > 0.5  # small aim wobble
    assert scene_similarity(still, scene_thumb(view(450))) < 0.5  # a real turn


def test_scene_similarity_ignores_a_burst_in_the_middle():
    frame = textured_view()[:, :700]
    burst = frame.copy()
    burst[120:280, 240:460] = 255
    assert scene_similarity(scene_thumb(frame), scene_thumb(burst)) > 0.9


def test_scene_similarity_trusts_flat_views():
    flat = np.full((400, 700), 120, np.uint8)
    assert scene_similarity(scene_thumb(flat), scene_thumb(flat)) == 1.0


def make_scene(rng, with_prompt, template):
    scene = rng.integers(0, 256, (400, 700), dtype=np.uint8)
    scene = cv2.GaussianBlur(scene, (9, 9), 0)
    if with_prompt:
        height, width = template.shape
        scene[220:220 + height, 310:310 + width] = template
    return scene


def test_matcher_finds_prompt_only_when_present():
    template = np.full((30, 90), 30, dtype=np.uint8)
    cv2.putText(template, "Chest", (4, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.75, 255, 2)
    matcher = PromptMatcher([template], threshold=0.8)
    rng = np.random.default_rng(1)
    assert matcher.is_visible(make_scene(rng, True, template))
    assert not matcher.is_visible(make_scene(rng, False, template))
    assert matcher.best_score(make_scene(rng, True, template)) >= 0.8


def test_matcher_rejects_a_different_word():
    def word(text):
        image = np.full((30, 90), 30, dtype=np.uint8)
        cv2.putText(image, text, (4, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.75, 255, 2)
        return image

    matcher = PromptMatcher([word("Chest")], threshold=0.8)
    assert not matcher.is_visible(make_scene(np.random.default_rng(2), True, word("Ammo")))


def test_matcher_with_blank_frame_and_template_larger_than_frame():
    template = np.full((30, 90), 30, dtype=np.uint8)
    cv2.putText(template, "Chest", (4, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.75, 255, 2)
    matcher = PromptMatcher([template], threshold=0.8)
    assert not matcher.is_visible(np.zeros((400, 700), dtype=np.uint8))
    assert not matcher.is_visible(np.zeros((10, 10), dtype=np.uint8))


def test_match_meter_maps_the_full_sensitivity_range():
    assert MatchMeter._threshold_at_fraction(-1) == MatchMeter.MIN_THRESHOLD
    assert MatchMeter._threshold_at_fraction(2) == MatchMeter.MAX_THRESHOLD
    assert MatchMeter._fraction_at_threshold(MatchMeter.MIN_THRESHOLD) == 0
    assert MatchMeter._fraction_at_threshold(MatchMeter.MAX_THRESHOLD) == 1


def test_protocol_round_trip_and_rejects_other_text():
    assert parse_delta(format_delta(3)) == 3
    assert parse_delta(format_delta(-1)) == -1
    assert parse_delta("hello (+5)") is None


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.db")


def message(id_, name, content, webhook_id=99):
    return SimpleNamespace(
        id=id_,
        webhook_id=webhook_id,
        content=content,
        author=SimpleNamespace(name=name),
        created_at=datetime.now(timezone.utc),
    )


def test_ingest_counts_webhook_posts_once_and_ignores_others(store):
    assert ingest(store, message(1, "Alex", format_delta(2)))
    assert not ingest(store, message(1, "Alex", format_delta(2)))
    assert not ingest(store, message(2, "Alex", format_delta(5), webhook_id=None))
    assert not ingest(store, message(3, "Alex", "just chatting"))
    assert store.total("Alex") == 2


def test_totals_today_leaderboard_and_case_insensitive_names(store):
    ingest(store, message(1, "Alex", format_delta(3)))
    ingest(store, message(2, "alex", format_delta(-1)))
    ingest(store, message(3, "Sam", format_delta(5)))
    store.add_manual("Old", 1, 86400)
    assert store.total("ALEX") == 2
    assert store.total() == 8
    assert store.today() == 7
    assert store.leaderboard() == [("Sam", 5), ("Alex", 2), ("Old", 1)]


def test_reset_does_not_move_the_catch_up_position_back(store):
    ingest(store, message(50, "Alex", format_delta(1)))
    store.set_last_message_id(50)
    store.set_last_message_id(10)
    assert store.reset_player("Alex") == 1
    assert store.total("Alex") == 0
    assert store.get_last_message_id() == 50


@pytest.mark.parametrize(
    "name, code", [
        ("E", 0x45), ("e", 0x45), ("5", 0x35), ("F9", 0x78),
        ("mouse4", 0x05), ("Backspace", 0x08), ("F10", 0x79),
    ]
)
def test_key_names(name, code):
    assert key_code(name) == code


def test_calibration_rejects_big_selections_at_any_resolution():
    assert not selection_too_big(54, 19, screen_height=1080)
    assert selection_too_big(417, 487, screen_height=1080)
    assert not selection_too_big(108, 38, screen_height=2160)


def test_unknown_key_is_rejected():
    with pytest.raises(ValueError):
        key_code("WHEELUP")


class FakeDiscord(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.bodies.append(body)
        status, reply = self.server.replies.pop(0)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(reply).encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_discord():
    server = HTTPServer(("127.0.0.1", 0), FakeDiscord)
    server.bodies, server.replies = [], []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


def test_sender_delivers_and_clears_pending(fake_discord, tmp_path):
    fake_discord.replies = [(200, {})]
    sender = Sender(f"http://127.0.0.1:{fake_discord.server_port}", "Alex", tmp_path / "p.json")
    sender.add(2)
    assert sender.send_once() == 0
    assert fake_discord.bodies == [{"username": "Alex", "content": format_delta(2)}]
    assert sender.pending == 0


def test_sender_waits_when_rate_limited_and_keeps_counts_on_errors(fake_discord, tmp_path):
    fake_discord.replies = [(429, {"retry_after": 2}), (404, {}), (200, {})]
    sender = Sender(f"http://127.0.0.1:{fake_discord.server_port}", "Alex", tmp_path / "p.json")
    sender.add(1)
    assert sender.send_once() == pytest.approx(2.1)
    assert sender.send_once() == 60
    assert sender.pending == 1
    assert sender.send_once() == 0
    assert sender.pending == 0


def test_sender_keeps_pending_counts_across_restarts(tmp_path):
    path = tmp_path / "p.json"
    Sender("http://127.0.0.1:9", "Alex", path).add(4)
    assert Sender("http://127.0.0.1:9", "Alex", path).pending == 4


def test_best_monitor_follows_the_window():
    monitors = [
        {"left": 3440, "top": -297, "width": 1080, "height": 1920},
        {"left": 0, "top": 0, "width": 3440, "height": 1440},
        {"left": -1920, "top": 139, "width": 1920, "height": 1080},
    ]
    assert best_monitor((-1928, 131, 8, 1179), monitors) is monitors[2]
    assert best_monitor((100, 100, 900, 700), monitors) is monitors[1]


def test_stats_add_clamp_persist_and_roll_over(tmp_path):
    stats = Stats(tmp_path)
    stats.add(2)
    stats.add(-5)
    stats.add(3)
    assert (stats.total, stats.today, stats.session) == (3, 3, 3)

    reloaded = Stats(tmp_path)
    assert (reloaded.total, reloaded.today, reloaded.session) == (3, 3, 0)

    reloaded.roll_over(date(2099, 1, 1))
    assert (reloaded.total, reloaded.today) == (3, 0)


def test_settings_save_and_keep_defaults(tmp_path):
    Settings(tmp_path).set(player_name="Sam")
    settings = Settings(tmp_path)
    assert settings["player_name"] == "Sam"
    assert settings["interact_keys"] == ["E"]
    assert settings["remove_key"] == "F10"
    assert settings["overlay_enabled"] is False
    assert settings["overlay_position"] == [0.76, 0.06]
    assert settings["fortnite_account_type"] == "epic"
    assert settings["fortnite_stats_window"] == "lifetime"


def make_old_setup(folder):
    (folder / "config.json").write_text(json.dumps({
        "player_name": "Robin", "webhook_url": "https://x/api/webhooks/1/a",
        "interact_keys": ["WHEELUP"], "match_threshold": 0.85,
        "search_area": [0.1, 0.1, 0.9, 0.9], "monitor": 3, "debug": False,
    }))
    (folder / ".env").write_text("DISCORD_TOKEN=abc=def\nCHANNEL_ID=123\n")
    (folder / "templates").mkdir()
    (folder / "templates" / "chest_1.png").write_bytes(b"png")
    (folder / "pending.json").write_text(json.dumps({"pending": 2}))
    store = Store(folder / "chests.db")
    store.record_message(1, "Robin", 7, int(datetime.now(timezone.utc).timestamp()))
    store.record_message(2, "Other", 3, int(datetime.now(timezone.utc).timestamp()))
    store.db.close()


def test_old_setup_is_imported_once(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    make_old_setup(old)

    assert import_old_setup(old, new)
    settings = Settings(new)
    assert settings["player_name"] == "Robin"
    assert settings["interact_keys"] == ["WHEELUP"]
    assert settings["match_threshold"] == 0.85
    assert settings["bot_token"] == "abc=def"
    assert settings["channel_id"] == "123"
    assert settings["run_bot"] is True
    assert "monitor" not in settings.values
    assert (new / "templates" / "chest_1.png").exists()
    assert (new / "pending.json").exists()
    assert Stats(new).total == 7

    settings.set(player_name="Changed")
    assert not import_old_setup(old, new)
    assert Settings(new)["player_name"] == "Changed"


def test_no_old_setup_means_nothing_to_import(tmp_path):
    assert not import_old_setup(tmp_path, tmp_path)
    assert not (tmp_path / "settings.json").exists()


def test_read_only_store_sees_data_and_cannot_write(tmp_path):
    path = tmp_path / "folder with spaces" / "c.db"
    path.parent.mkdir()
    writer = Store(path)
    writer.add_manual("Sam", 4, 86400)
    reader = Store.read_only(path)
    assert reader.leaderboard() == [("Sam", 4)]
    with pytest.raises(sqlite3.OperationalError):
        reader.add_manual("Sam", 1, 86400)
    reader.db.close()
    writer.db.close()


class FakeCapture:
    """Plays back screen frames in place of the real monitor."""

    frames = []

    def __init__(self, area):
        self.index = 0

    def refresh(self):
        pass

    def grab(self):
        frame = self.frames[min(self.index, len(self.frames) - 1)]
        self.index += 1
        return frame

    def close(self):
        pass


def run_engine(matcher, capture, interact, seconds, focused=lambda: True):
    chests, states, attempts = [], [], []
    engine = WatchEngine(
        matcher, interact, [0, 0, 1, 1],
        on_chest=lambda amount, source: chests.append((amount, source)),
        on_state=states.append,
        on_score=lambda score: None,
        on_attempt=attempts.append,
        focused=focused,
        capture_factory=capture,
        remove_hotkey=0x07,  # an unassigned key code, so real keys can't interfere
    )
    engine.start()
    time.sleep(seconds)
    engine.stop()
    engine.join(2)
    assert not engine.is_alive()
    return chests, states, attempts


def prompt_scenes():
    template = np.full((30, 90), 30, dtype=np.uint8)
    cv2.putText(template, "SEARCH", (2, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, 255, 2)
    world = textured_view()
    without = world[:, 100:800].copy()
    with_prompt = without.copy()
    with_prompt[220:250, 400:490] = template
    turned = world[:, 600:1300].copy()
    return template, with_prompt, without, turned


@pytest.fixture
def quick_confirm(monkeypatch):
    monkeypatch.setattr(ChestDetector, "CONFIRM_SECONDS", 0.8)
    monkeypatch.setattr(ChestDetector, "COOLDOWN", 0.1)


def presses():
    sequence = iter([False, False, True])
    return lambda: next(sequence, False)


def test_engine_counts_one_chest_after_a_finished_search(quick_confirm):
    template, with_prompt, without, _ = prompt_scenes()

    class Capture(FakeCapture):
        frames = [with_prompt] * 4 + [without]

    chests, states, attempts = run_engine(PromptMatcher([template], 0.8), Capture, presses(), 1.6)
    assert chests == [(1, "detected")]
    assert states == ["watching"]
    assert attempts == [PENDING]


def test_engine_does_not_count_when_the_player_turns_away(quick_confirm):
    template, with_prompt, _, turned = prompt_scenes()

    class Capture(FakeCapture):
        frames = [with_prompt] * 4 + [turned]

    chests, _, attempts = run_engine(PromptMatcher([template], 0.8), Capture, presses(), 1.6)
    assert chests == []
    assert attempts == [PENDING, CANCELLED]


def test_engine_does_not_count_when_the_prompt_comes_back(quick_confirm):
    template, with_prompt, without, _ = prompt_scenes()

    class Capture(FakeCapture):
        frames = [with_prompt] * 4 + [without] * 6 + [with_prompt]

    chests, _, attempts = run_engine(PromptMatcher([template], 0.8), Capture, presses(), 1.6)
    assert chests == []
    assert CANCELLED in attempts


def test_engine_ignores_walking_past_and_outside_the_game(quick_confirm):
    template, with_prompt, without, _ = prompt_scenes()

    class Capture(FakeCapture):
        frames = [with_prompt] * 4 + [without]

    chests, _, _ = run_engine(PromptMatcher([template], 0.8), Capture, lambda: False, 1.0)
    assert chests == []

    chests, states, _ = run_engine(
        PromptMatcher([template], 0.8), Capture, lambda: True, 0.5, focused=lambda: False
    )
    assert chests == []
    assert states == ["waiting"]


def test_undo_key_press_outside_fortnite_is_not_applied_after_focus_returns(monkeypatch):
    key_events = iter([True, False, False, False, False, False])

    class ScriptedKeyEdge:
        def __init__(self, code):
            self.code = code

        def pressed(self):
            return next(key_events, False)

    monkeypatch.setattr(watcher, "KeyEdge", ScriptedKeyEdge)
    template, _, frame, _ = prompt_scenes()

    class Capture(FakeCapture):
        frames = [frame]

    focus_events = iter([False, True, True, True])
    chests, _, _ = run_engine(
        PromptMatcher([template], 0.8), Capture, lambda: False, 0.4,
        focused=lambda: next(focus_events, True),
    )
    assert chests == []


def test_undo_key_press_in_fortnite_removes_one_chest(monkeypatch):
    key_events = iter([True, False, False, False, False])

    class ScriptedKeyEdge:
        def __init__(self, code):
            self.code = code

        def pressed(self):
            return next(key_events, False)

    monkeypatch.setattr(watcher, "KeyEdge", ScriptedKeyEdge)
    template, _, frame, _ = prompt_scenes()

    class Capture(FakeCapture):
        frames = [frame]

    chests, _, _ = run_engine(
        PromptMatcher([template], 0.8), Capture, lambda: False, 0.15,
        focused=lambda: True,
    )
    assert chests == [(-1, "hotkey")]


def test_engine_survives_capture_failures():
    template, _, without, _ = prompt_scenes()

    class Capture(FakeCapture):
        calls = 0

        def grab(self):
            Capture.calls += 1
            if Capture.calls == 1:
                raise OSError("screen locked")
            return without

    _, states, _ = run_engine(PromptMatcher([template], 0.8), Capture, lambda: False, 1.3)
    assert Capture.calls > 1
    assert states == ["watching"]


def test_engine_reports_a_blank_capture(monkeypatch):
    template, _, _, _ = prompt_scenes()
    monkeypatch.setattr(WatchEngine, "BLACK_SECONDS", 0.2)

    class Capture(FakeCapture):
        frames = [np.zeros((400, 700), dtype=np.uint8)]

    _, states, _ = run_engine(PromptMatcher([template], 0.8), Capture, lambda: False, 0.6)
    assert states == ["watching", "black"]


def test_flat_frames_are_never_mistaken_for_the_prompt():
    template, *_ = prompt_scenes()
    matcher = PromptMatcher([template], 0.8)
    for level in (0, 120, 255):  # black screen, plain sky, white-out
        assert matcher.best_score(np.full((400, 700), level, np.uint8)) < 0.8


def test_stats_add_reports_what_really_changed(tmp_path):
    stats = Stats(tmp_path)
    assert stats.add(-1) == 0  # nothing to take off
    assert stats.add(3) == 3
    assert stats.add(-5) == -3  # clamped at zero, so only 3 really went away
    assert stats.total == 0


def test_damaged_files_do_not_stop_the_app_starting(tmp_path):
    (tmp_path / "settings.json").write_text("{not json")
    (tmp_path / "stats.json").write_text('{"total": "lots"}')
    (tmp_path / "pending.json").write_text("[1, 2")
    settings = Settings(tmp_path)
    assert settings["remove_key"] == "F10"
    stats = Stats(tmp_path)
    assert (stats.total, stats.today) == (0, 0)
    assert Sender("http://127.0.0.1:9", "Alex", tmp_path / "pending.json").pending == 0
    assert (tmp_path / "settings.json.damaged").exists()  # kept, not deleted


def test_settings_do_not_share_state_between_instances(tmp_path):
    first, second = Settings(tmp_path), Settings(tmp_path)
    first["search_area"].append(9)
    assert second["search_area"] == [0.15, 0.15, 0.85, 0.95]


@pytest.mark.parametrize("name", ["Alex", "  Sam  ", "", "Dis cord", "x" * 32])
def test_player_names_discord_accepts(name):
    assert name_problem(name) is None


@pytest.mark.parametrize("name", ["Discord", "my_DISCORD_name", "xClydex", "x" * 33])
def test_player_names_discord_refuses(name):
    assert name_problem(name)


@pytest.mark.parametrize("url,ok", [
    ("https://discord.com/api/webhooks/1/abc", True),
    ("https://canary.discord.com/api/webhooks/1/abc", True),
    ("https://discordapp.com/api/webhooks/1/abc", True),
    ("http://discord.com/api/webhooks/1/abc", False),
    ("https://evil.example/api/webhooks/1/abc", False),
    ("https://discord.com.evil.example/api/webhooks/1/abc", False),
    ("https://evil.example/?next=https://discord.com/api/webhooks/1/a", False),
    ("https://discord.com/other", False),
    ("", False),
])
def test_webhook_url_must_be_a_discord_link(url, ok):
    assert is_webhook_url(url) is ok


def test_check_webhook_never_contacts_a_non_discord_host(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("must not be called")

    monkeypatch.setattr(watcher.requests, "get", boom)
    assert check_webhook("https://evil.example/api/webhooks/1/abc")[0] is False


def test_sender_explains_a_refused_player_name(fake_discord, tmp_path):
    fake_discord.replies = [(400, {"username": ["Username cannot contain \"discord\""]}), (200, {})]
    results = []
    sender = Sender(
        f"http://127.0.0.1:{fake_discord.server_port}", "Discord", tmp_path / "p.json",
        lambda ok, message: results.append((ok, message)),
    )
    sender.add(1)
    assert sender.send_once() == 60
    assert results[-1][0] is False and "player name" in results[-1][1]
    assert sender.pending == 1  # kept, and delivered once the name is fixed


def test_sender_explains_a_deleted_webhook(fake_discord, tmp_path):
    fake_discord.replies = [(404, {"message": "Unknown Webhook"})]
    results = []
    sender = Sender(
        f"http://127.0.0.1:{fake_discord.server_port}", "Alex", tmp_path / "p.json",
        lambda ok, message: results.append((ok, message)),
    )
    sender.add(1)
    sender.send_once()
    assert "webhook" in results[-1][1].lower()


def test_wheel_listener_that_fails_to_start_is_retried(monkeypatch):
    attempts = []

    class Flaky:
        def start(self):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("Windows refused raw input")

    monkeypatch.setattr(watcher, "_wheel", None)
    monkeypatch.setattr(watcher, "WheelListener", Flaky)
    with pytest.raises(RuntimeError):
        watcher._shared_wheel()
    assert watcher._wheel is None  # the failed listener was not kept
    assert isinstance(watcher._shared_wheel(), Flaky)  # the next try starts fresh
    assert len(attempts) == 2


def test_engine_logs_how_an_attempt_went(quick_confirm, caplog):
    import logging
    template, with_prompt, without, _ = prompt_scenes()

    class Capture(FakeCapture):
        frames = [with_prompt] * 4 + [without]

    with caplog.at_level(logging.INFO, logger="chest-tracker"):
        chests, _, _ = run_engine(PromptMatcher([template], 0.8), Capture, presses(), 1.6)
    assert chests == [(1, "detected")]
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Attempt opened"))
    assert "key used for" in line and "prompt gone" in line and "lowest aim match" in line


def test_a_quick_undo_after_a_count_is_logged_for_tuning(monkeypatch, caplog):
    import logging
    template, with_prompt, without, _ = prompt_scenes()
    monkeypatch.setattr(ChestDetector, "CONFIRM_SECONDS", 0.5)
    monkeypatch.setattr(ChestDetector, "COOLDOWN", 0.1)
    key_events = iter([False] * 30 + [True])  # undo pressed well after the chest counted

    class ScriptedKeyEdge:
        def __init__(self, code):
            pass

        def pressed(self):
            return next(key_events, False)

    monkeypatch.setattr(watcher, "KeyEdge", ScriptedKeyEdge)

    class Capture(FakeCapture):
        frames = [with_prompt] * 4 + [without]

    with caplog.at_level(logging.INFO, logger="chest-tracker"):
        chests, _, _ = run_engine(PromptMatcher([template], 0.8), Capture, presses(), 2.5)
    assert (1, "detected") in chests
    assert any("Undo pressed" in r.getMessage() for r in caplog.records)


def make_engine(matcher, capture, interact, **extra):
    chests, states, attempts, pauses = [], [], [], []
    extra.setdefault("focused", lambda: True)  # as if Fortnite were in front, unless a test says otherwise
    engine = WatchEngine(
        matcher, interact, [0, 0, 1, 1],
        on_chest=lambda amount, source: chests.append((amount, source)),
        on_state=states.append,
        on_score=lambda score: None,
        on_attempt=attempts.append,
        capture_factory=capture,
        remove_hotkey=0x07,
        on_pause=lambda: pauses.append(1),
        **extra,
    )
    return engine, chests, states, attempts, pauses


def test_a_paused_engine_counts_nothing_and_says_so(quick_confirm):
    template, with_prompt, without, _ = prompt_scenes()

    class Capture(FakeCapture):
        frames = [with_prompt] * 4 + [without]

    engine, chests, states, attempts, _ = make_engine(PromptMatcher([template], 0.8), Capture, lambda: True)
    engine.paused.set()
    engine.start()
    time.sleep(1.2)
    engine.stop()
    engine.join(2)
    assert chests == [] and attempts == []
    assert states == ["off"]


def test_resuming_counts_again_and_presses_made_while_paused_are_forgotten(quick_confirm):
    template, with_prompt, without, _ = prompt_scenes()

    class Capture(FakeCapture):
        frames = [with_prompt] * 4 + [without]

    holder = {}
    since_resume = {"n": 0}

    def interact():
        if holder["engine"].paused.is_set():
            return True  # held down the whole time it was paused; must not carry over
        since_resume["n"] += 1
        return since_resume["n"] == 3

    engine, chests, states, _, _ = make_engine(PromptMatcher([template], 0.8), Capture, interact)
    holder["engine"] = engine
    engine.paused.set()
    engine.start()
    time.sleep(0.4)
    assert chests == []
    engine.paused.clear()
    time.sleep(1.6)
    engine.stop()
    engine.join(2)
    assert chests == [(1, "detected")]
    assert states[0] == "off" and states[-1] == "watching"


def test_pausing_in_the_middle_of_a_search_cancels_it(quick_confirm):
    template, with_prompt, _, _ = prompt_scenes()

    class Capture(FakeCapture):
        frames = [with_prompt]

    engine, chests, _, attempts, _ = make_engine(PromptMatcher([template], 0.8), Capture, presses())
    engine.start()
    time.sleep(0.5)
    engine.paused.set()
    time.sleep(0.4)
    engine.stop()
    engine.join(2)
    assert chests == []
    assert attempts == [CANCELLED]  # the search that was under way is dropped, not counted


def scripted_pause_key(monkeypatch, events):
    class ScriptedKeyEdge:
        def __init__(self, code):
            self.code = code

        def pressed(self):
            return next(events, False) if self.code == 0x0A else False

    monkeypatch.setattr(watcher, "KeyEdge", ScriptedKeyEdge)


def test_the_pause_key_reports_a_press_in_fortnite(monkeypatch):
    scripted_pause_key(monkeypatch, iter([False, True, False]))
    template, _, frame, _ = prompt_scenes()

    class Capture(FakeCapture):
        frames = [frame]

    engine, _, _, _, pauses = make_engine(
        PromptMatcher([template], 0.8), Capture, lambda: False, pause_hotkey=0x0A
    )
    engine.start()
    time.sleep(0.5)
    engine.stop()
    engine.join(2)
    assert pauses == [1]


def test_the_pause_key_is_ignored_outside_fortnite(monkeypatch):
    scripted_pause_key(monkeypatch, iter([True, False, False]))
    template, _, frame, _ = prompt_scenes()

    class Capture(FakeCapture):
        frames = [frame]

    engine, _, _, _, pauses = make_engine(
        PromptMatcher([template], 0.8), Capture, lambda: False, pause_hotkey=0x0A, focused=lambda: False
    )
    engine.start()
    time.sleep(0.5)
    engine.stop()
    engine.join(2)
    assert pauses == []


def test_the_pause_key_still_works_while_paused(monkeypatch):
    scripted_pause_key(monkeypatch, iter([False] * 5 + [True]))
    template, _, frame, _ = prompt_scenes()

    class Capture(FakeCapture):
        frames = [frame]

    engine, _, _, _, pauses = make_engine(
        PromptMatcher([template], 0.8), Capture, lambda: False, pause_hotkey=0x0A
    )
    engine.paused.set()
    engine.start()
    time.sleep(0.6)
    engine.stop()
    engine.join(2)
    assert pauses == [1]
