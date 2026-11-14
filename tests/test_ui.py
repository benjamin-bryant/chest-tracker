"""Window-level behavior, run headless. Skipped where PySide6 isn't installed."""

import os
import sys
import threading
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
        self.paused = threading.Event()
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
    window.settings.set(remove_key="F6")
    window.restart_engine()
    assert FakeEngine.instances[-1].kwargs["remove_hotkey"] == 0x75


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
    window.setup._save_remove_key("F6")
    assert window.settings["remove_key"] == "F6"


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


def refuse_keys(monkeypatch):
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: warned.append(args[1]))
    return warned


def test_the_engine_gets_the_pause_key_and_a_way_to_report_it(window, monkeypatch):
    monkeypatch.setattr(ui, "WatchEngine", FakeEngine)
    save_template(window)
    window.restart_engine()
    kwargs = FakeEngine.instances[-1].kwargs
    assert kwargs["pause_hotkey"] == ui.VK_F9
    assert kwargs["on_pause"] == window.pause_pressed.emit


def test_a_bad_pause_key_falls_back_to_f9(window, monkeypatch):
    monkeypatch.setattr(ui, "WatchEngine", FakeEngine)
    save_template(window)
    window.settings.set(pause_key="NOT-A-KEY")
    window.restart_engine()
    assert window.settings["pause_key"] == "F9"
    assert FakeEngine.instances[-1].kwargs["pause_hotkey"] == ui.VK_F9


def test_pause_undo_and_interact_keys_must_all_differ(window, monkeypatch):
    monkeypatch.setattr(ui, "WatchEngine", FakeEngine)
    warned = refuse_keys(monkeypatch)
    window.setup._save_pause_key("F10")  # undo's key
    window.setup._save_pause_key("E")  # interact's key
    window.setup._save_remove_key("F9")  # pause's key
    window.setup._save_key("F9")
    window.setup._save_key("F10")
    assert len(warned) == 5
    assert window.settings["pause_key"] == "F9"
    assert window.settings["remove_key"] == "F10"
    assert window.settings["interact_keys"] == ["E"]


def test_a_different_pause_key_is_saved_and_used(window, monkeypatch):
    monkeypatch.setattr(ui, "WatchEngine", FakeEngine)
    save_template(window)
    window.setup._save_pause_key("F7")
    assert window.settings["pause_key"] == "F7"
    assert FakeEngine.instances[-1].kwargs["pause_hotkey"] == 0x76


def test_a_typing_key_for_pause_asks_first(window, monkeypatch):
    monkeypatch.setattr(ui, "WatchEngine", FakeEngine)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)
    window.setup._save_pause_key("P")
    assert window.settings["pause_key"] == "F9"


def test_pausing_stops_counting_without_stopping_the_engine(window, monkeypatch):
    monkeypatch.setattr(ui, "WatchEngine", FakeEngine)
    save_template(window)
    window.restart_engine()
    engine = FakeEngine.instances[-1]
    window.toggle_pause()
    assert window.paused and engine.paused.is_set()
    assert window.overlay.paused
    assert window.tracker.pause.text() == "Resume tracking"
    assert window.current_state == "off"
    window.toggle_pause()
    assert not window.paused and not engine.paused.is_set()
    assert not window.overlay.paused
    assert window.tracker.pause.text() == "Pause tracking"
    assert FakeEngine.instances[-1] is engine  # no restart was needed


def test_the_pause_key_flips_the_pause_state(window, monkeypatch):
    monkeypatch.setattr(ui, "WatchEngine", FakeEngine)
    save_template(window)
    window.restart_engine()
    window.pause_pressed.emit()
    assert window.paused
    window.pause_pressed.emit()
    assert not window.paused


def test_a_new_engine_starts_paused_when_the_app_is_paused(window, monkeypatch):
    monkeypatch.setattr(ui, "WatchEngine", FakeEngine)
    save_template(window)
    window.restart_engine()
    window.toggle_pause()
    window.settings.set(remove_key="F6")
    window.restart_engine()  # e.g. a setting changed while paused
    assert FakeEngine.instances[-1].paused.is_set()
    assert window.paused


def test_resuming_with_no_engine_starts_one(window, monkeypatch):
    monkeypatch.setattr(ui, "WatchEngine", FakeEngine)
    window.paused = True
    window.toggle_pause()  # setup unfinished, so there was no engine
    assert not window.paused


class Recorder:
    def __init__(self):
        self.played = []

    def __call__(self, data):
        self.played.append(data)


def test_the_chime_plays_for_a_counted_chest_only_when_turned_on(window):
    recorder = Recorder()
    window.chime = ui.Chime(player=recorder)
    window.record_chest(1, "detected")
    assert recorder.played == []  # off by default
    window.settings.set(chime_enabled=True)
    window.record_chest(1, "detected")
    assert len(recorder.played) == 1
    window.record_chest(-1, "hotkey")  # an undo is not a celebration
    assert len(recorder.played) == 1


def test_no_chime_when_nothing_changed(window):
    recorder = Recorder()
    window.chime = ui.Chime(player=recorder)
    window.settings.set(chime_enabled=True)
    window.stats.add(-window.stats.total)
    window.record_chest(-1, "hotkey")
    assert recorder.played == []


def test_the_test_button_plays_at_the_chosen_volume(window):
    recorder = Recorder()
    window.chime = ui.Chime(player=recorder)
    window.settings.set(chime_volume=0.3)
    window.play_chime()
    window.play_chime(volume=0.9)
    assert len(recorder.played) == 2
    assert recorder.played[0] != recorder.played[1]  # a louder sound is a different recording


def test_a_broken_sound_system_does_not_stop_counting(window):
    def broken(data):
        raise OSError("no sound device")

    window.chime = ui.Chime(player=broken)
    window.settings.set(chime_enabled=True)
    window.record_chest(1, "detected")
    assert window.stats.total == 1


def test_overlay_size_and_opacity_apply_and_are_saved(window):
    window.set_overlay_scale(1.5)
    window.set_overlay_opacity(0.6)
    assert window.overlay.width() == round(220 * 1.5) and window.overlay.height() == round(76 * 1.5)
    assert window.overlay.opacity == 0.6
    assert window.settings["overlay_scale"] == 1.5
    assert window.settings["overlay_opacity"] == 0.6


def test_overlay_options_are_kept_in_range(window):
    window.set_overlay_scale(50)
    window.set_overlay_opacity(-3)
    assert window.overlay.scale == 2.0
    assert window.overlay.opacity == 0.3


def test_dragging_a_slider_previews_without_saving_until_released(window):
    window.set_overlay_scale(1.8, save=False)
    assert window.overlay.scale == 1.8
    assert window.settings["overlay_scale"] == 1.0
    window.set_overlay_scale(1.8)
    assert window.settings["overlay_scale"] == 1.8


def test_damaged_overlay_settings_fall_back_instead_of_crashing(app, tmp_path):
    (tmp_path / "settings.json").write_text(
        '{"overlay_scale": "huge", "overlay_opacity": null, "chime_volume": "loud"}', encoding="utf-8"
    )
    main = ui.MainWindow(tmp_path, QIcon())
    try:
        assert main.overlay.scale == 1.0 and main.overlay.opacity == 1.0
        assert main.setup.volume_slider.value() == 60
    finally:
        main._shut_down()


def test_setup_sliders_start_where_the_settings_are(app, tmp_path):
    (tmp_path / "settings.json").write_text(
        '{"overlay_scale": 1.5, "overlay_opacity": 0.5, "chime_volume": 0.25}', encoding="utf-8"
    )
    main = ui.MainWindow(tmp_path, QIcon())
    try:
        assert main.setup.size_slider.value() == 150
        assert main.setup.opacity_slider.value() == 50
        assert main.setup.volume_slider.value() == 25
        assert main.overlay.scale == 1.5
    finally:
        main._shut_down()


def test_the_overlay_says_when_it_is_paused(window):
    window.toggle_pause()
    image = window.overlay.grab().toImage()
    assert not image.isNull()
    window.toggle_pause()
