"""Window-level behavior, run headless. Skipped where PySide6 isn't installed."""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

import ui


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    main = ui.MainWindow(tmp_path, QIcon())
    yield main
    main._shut_down()


def share_to_discord(window):
    window.settings.set(webhook_url="https://discord.com/api/webhooks/1/x", player_name="Sam")


def test_removing_a_chest_at_zero_sends_nothing_to_discord(window):
    share_to_discord(window)
    window.record_chest(-1, "hotkey")
    window.record_chest(-1, "hotkey")
    assert window.stats.total == 0
    assert window.sender.pending == 0


def test_discord_total_follows_the_local_total(window):
    share_to_discord(window)
    window.sender.wake.clear()
    for amount in (1, 1, -1, -1, -1, 1):
        window.record_chest(amount, "detected" if amount > 0 else "hotkey")
    assert window.stats.total == 1
    assert window.sender.pending == 1  # what Discord is still owed matches what is really counted


def test_local_only_mode_queues_nothing(window):
    window.record_chest(1, "detected")
    assert window.stats.total == 1
    assert window.sender.pending == 0


def test_player_name_discord_would_refuse_is_not_saved(window):
    page = window.setup
    page.name.setText("Discord")
    page._save_name()
    assert window.settings["player_name"] == ""
    assert not page.name_status.isHidden()
    page.name.setText("Sam")
    page._save_name()
    assert window.settings["player_name"] == "Sam"
    assert page.name_status.isHidden()


def test_prompt_dialog_stops_listening_for_f8_once_closed(window):
    dialog = ui.PromptDialog(window.setup, window.templates_dir, window.settings["search_area"])
    assert dialog.timer.isActive()
    dialog.reject()
    assert not dialog.timer.isActive()


def test_prompt_dialog_stops_listening_after_saving(window):
    dialog = ui.PromptDialog(window.setup, window.templates_dir, window.settings["search_area"])
    dialog.accept()
    assert not dialog.timer.isActive()


class FakeEngine:
    instances = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        FakeEngine.instances.append(self)

    def start(self):
        pass

    def stop(self):
        pass

    def join(self, timeout=None):
        pass


def save_template(window):
    import cv2
    import numpy as np

    window.templates_dir.mkdir(exist_ok=True)
    cv2.imwrite(str(window.templates_dir / "chest_1.png"), np.full((20, 60), 90, np.uint8))


def test_a_bad_undo_key_falls_back_to_f10_instead_of_crashing(window, monkeypatch):
    monkeypatch.setattr(ui, "WatchEngine", FakeEngine)
    save_template(window)
    window.settings.set(remove_key="NOT-A-KEY")
    window.restart_engine()
    assert window.settings["remove_key"] == "F10"
    assert FakeEngine.instances[-1].kwargs["remove_hotkey"] == ui.VK_F10


def test_the_engine_uses_the_saved_undo_key(window, monkeypatch):
    monkeypatch.setattr(ui, "WatchEngine", FakeEngine)
    save_template(window)
    window.settings.set(remove_key="F9")
    window.restart_engine()
    assert FakeEngine.instances[-1].kwargs["remove_hotkey"] == 0x78


def test_an_out_of_range_threshold_is_clamped_for_the_engine(window, monkeypatch):
    seen = []
    real = ui.PromptMatcher.from_folder
    monkeypatch.setattr(ui, "WatchEngine", FakeEngine)
    monkeypatch.setattr(ui.PromptMatcher, "from_folder", classmethod(lambda cls, f, t: seen.append(t) or real(f, t)))
    save_template(window)
    window.settings.set(match_threshold=0.2)
    window.restart_engine()
    assert seen[-1] == 0.5


@pytest.mark.parametrize("answer,saved", [(QMessageBox.StandardButton.No, "F10"), (QMessageBox.StandardButton.Yes, "BACKSPACE")])
def test_choosing_a_typing_key_for_undo_asks_first(window, monkeypatch, answer, saved):
    monkeypatch.setattr(ui, "WatchEngine", FakeEngine)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: answer)
    window.setup._save_remove_key("BACKSPACE")
    assert window.settings["remove_key"] == saved


def test_choosing_a_function_key_for_undo_needs_no_question(window, monkeypatch):
    monkeypatch.setattr(ui, "WatchEngine", FakeEngine)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: pytest.fail("should not ask"))
    window.setup._save_remove_key("F9")
    assert window.settings["remove_key"] == "F9"


def test_every_page_builds_and_the_overlay_follows_the_count(window):
    for index in range(4):
        window.go_to(index)
    window.record_chest(1, "detected")
    assert window.overlay.count == 1


class FakeListener:
    def __init__(self):
        self.queue = []

    def take_any(self):
        return self.queue.pop(0) if self.queue else None


def test_setup_key_picker_takes_a_controller_button(app, monkeypatch):
    fake = FakeListener()
    monkeypatch.setattr(ui, "shared_gamepad", lambda: fake)
    capture = ui.KeyCapture()
    chosen = []
    capture.captured.connect(chosen.append)
    capture._listen()
    assert "controller button" in capture.text()
    capture._check_pad()
    assert chosen == [] and capture.listening  # nothing pressed yet
    fake.queue.append("PAD_RT")
    capture._check_pad()
    assert chosen == ["PAD_RT"]
    assert not capture.listening and not capture.pad_timer.isActive()
    assert capture.text() == "Controller Right trigger"


def test_undo_key_picker_ignores_the_controller(app, monkeypatch):
    fake = FakeListener()
    fake.queue.append("PAD_A")
    monkeypatch.setattr(ui, "shared_gamepad", lambda: fake)
    capture = ui.KeyCapture(keyboard_only=True, action_label="undo key")
    capture._listen()
    assert not capture.pad_timer.isActive()
    assert "controller" not in capture.text()
    capture._finish(None)


def test_a_saved_controller_button_is_shown_by_name(window, monkeypatch):
    monkeypatch.setattr(ui, "shared_gamepad", lambda: FakeListener())
    window.settings.set(interact_keys=["PAD_A"])
    assert ui.key_label("PAD_A") == "Controller A button"
