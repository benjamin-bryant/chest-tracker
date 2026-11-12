import pytest

import gamepad
import watcher
from gamepad import GamepadListener, buttons_down, is_pad_name, pad_label


class FakePads:
    """Stands in for XInput: pad 0 shows whatever buttons the test sets, the rest are unplugged."""

    def __init__(self):
        self.pad0: set[str] | None = set()
        self.calls = [0, 0, 0, 0]

    def __call__(self, index):
        self.calls[index] += 1
        return self.pad0 if index == 0 else None


def test_buttons_are_read_from_the_bitmask():
    assert buttons_down(0x1000, 0, 0) == {"PAD_A"}
    assert buttons_down(0x4000 | 0x0200, 0, 0) == {"PAD_X", "PAD_RB"}
    assert buttons_down(0, 0, 0) == set()


def test_triggers_count_past_the_documented_threshold():
    assert buttons_down(0, 29, 0) == set()
    assert buttons_down(0, 30, 255) == {"PAD_LT", "PAD_RT"}


def test_names_and_labels():
    assert is_pad_name("pad_a") and is_pad_name("PAD_RT")
    assert not is_pad_name("E") and not is_pad_name("PAD_Q")
    assert pad_label("PAD_RT") == "Controller Right trigger"
    assert pad_label("PAD_A") == "Controller A button"


def test_held_button_reads_as_pressed():
    pads = FakePads()
    pads.pad0 = {"PAD_A"}
    listener = GamepadListener(pads)
    listener.poll_once(now=10.0)
    assert listener.pressed("PAD_A")
    assert listener.pressed("PAD_A")  # still held
    assert not listener.pressed("PAD_B")


def test_a_quick_tap_is_remembered_until_collected():
    pads = FakePads()
    listener = GamepadListener(pads)
    pads.pad0 = {"PAD_X"}
    listener.poll_once(now=10.0)
    pads.pad0 = set()  # released again before anyone asked
    listener.poll_once(now=10.01)
    assert listener.pressed("PAD_X")  # the tap was not lost
    assert not listener.pressed("PAD_X")  # and it only counts once


def test_unplugged_pads_are_only_rechecked_now_and_then():
    pads = FakePads()
    listener = GamepadListener(pads)
    for step in range(100):
        listener.poll_once(now=10.0 + step * 0.008)  # 0.8 seconds
    assert pads.calls[0] == 100  # the connected pad is read every time
    assert pads.calls[1] == 1  # an empty slot is asked about once, not 100 times
    listener.poll_once(now=11.5)
    assert pads.calls[1] == 2


def test_a_pad_plugged_in_later_is_picked_up():
    pads = FakePads()
    pads.pad0 = None
    listener = GamepadListener(pads)
    listener.poll_once(now=10.0)
    pads.pad0 = {"PAD_B"}
    listener.poll_once(now=10.2)
    assert not listener.pressed("PAD_B")  # not looked at yet
    listener.poll_once(now=11.5)
    assert listener.pressed("PAD_B")


def test_a_driver_error_does_not_stop_polling():
    def broken(index):
        raise OSError("driver went away")

    listener = GamepadListener(broken)
    listener.poll_once(now=10.0)
    assert not listener.pressed("PAD_A")


def test_without_xinput_nothing_happens():
    listener = GamepadListener(None)
    listener.poll_once(now=10.0)
    assert not listener.pressed("PAD_A")
    assert listener.take_any() is None


def test_take_any_returns_each_new_press_once():
    pads = FakePads()
    listener = GamepadListener(pads)
    listener.take_any()
    pads.pad0 = {"PAD_Y"}
    listener.poll_once(now=10.0)
    assert listener.take_any() == "PAD_Y"
    assert listener.take_any() is None
    listener.poll_once(now=10.01)  # still held: not a new press
    assert listener.take_any() is None


def test_interact_check_accepts_controller_buttons(monkeypatch):
    pads = FakePads()
    listener = GamepadListener(pads)
    monkeypatch.setattr(watcher, "shared_gamepad", lambda: listener)
    check = watcher.interact_check(["PAD_A"])
    assert not check()
    pads.pad0 = {"PAD_A"}
    listener.poll_once(now=10.0)
    assert check()


def test_interact_check_mixes_keyboard_and_controller(monkeypatch):
    pads = FakePads()
    listener = GamepadListener(pads)
    monkeypatch.setattr(watcher, "shared_gamepad", lambda: listener)
    monkeypatch.setattr(watcher, "key_down", lambda code: False)
    check = watcher.interact_check(["E", "PAD_RT"])
    pads.pad0 = {"PAD_RT"}
    listener.poll_once(now=10.0)
    assert check()


def test_interact_check_rejects_unknown_controller_buttons():
    with pytest.raises(ValueError):
        watcher.interact_check(["PAD_NOPE"])


def test_shared_gamepad_is_one_listener(monkeypatch):
    monkeypatch.setattr(gamepad, "_shared", None)
    monkeypatch.setattr(gamepad, "_load_xinput", lambda: None)
    first = gamepad.shared_gamepad()
    assert gamepad.shared_gamepad() is first
    first.stop()
