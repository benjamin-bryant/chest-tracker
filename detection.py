"""Decides when a chest was really opened from what is on screen and which keys are down."""

from pathlib import Path

import cv2
import numpy as np

PENDING = "pending"
OPENED = "opened"
CANCELLED = "cancelled"


class PromptMatcher:
    """Looks for the saved chest prompt image(s) in a grayscale screen frame."""

    def __init__(self, templates: list[np.ndarray], threshold: float):
        self.templates = templates
        self.threshold = threshold

    @classmethod
    def from_folder(cls, folder: Path, threshold: float) -> "PromptMatcher":
        paths = sorted(folder.glob("*.png"))
        templates = [cv2.imread(str(path), cv2.IMREAD_GRAYSCALE) for path in paths]
        return cls([t for t in templates if t is not None], threshold)

    def best_score(self, frame: np.ndarray) -> float:
        best = 0.0
        for template in self.templates:
            if template.shape[0] > frame.shape[0] or template.shape[1] > frame.shape[1]:
                continue
            result = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
            # A flat frame (black screen, plain sky) gives NaN scores: treat as no match.
            best = max(best, float(np.nan_to_num(result, nan=-1.0).max()))
        return best

    def is_visible(self, frame: np.ndarray) -> bool:
        return self.best_score(frame) >= self.threshold


def scene_thumb(frame: np.ndarray) -> np.ndarray:
    """A tiny copy of the view for comparing where the player is looking."""
    return cv2.resize(frame, (64, 36), interpolation=cv2.INTER_AREA).astype(np.float32)


def _correlation(a: np.ndarray, b: np.ndarray) -> float | None:
    a, b = a - a.mean(), b - b.mean()
    if a.std() < 3 or b.std() < 3:  # flat view like open sky: nothing reliable to compare
        return None
    return float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-6))


def scene_similarity(reference: np.ndarray, current: np.ndarray) -> float:
    """How much the view still matches, from 1 (same aim) downward.

    The middle is ignored, because the chest bursting open and the player's own
    character change there without the aim moving. The second comparison removes
    each row's average, otherwise sky above and ground below still match after a
    full turn.
    """
    keep = np.ones(reference.shape, dtype=bool)
    keep[9:27, 20:44] = False
    scores = [_correlation(reference[keep], current[keep])]
    sides = np.r_[0:20, 44:64]
    flat_a = reference - reference[:, sides].mean(axis=1, keepdims=True)
    flat_b = current - current[:, sides].mean(axis=1, keepdims=True)
    scores.append(_correlation(flat_a[keep], flat_b[keep]))
    known = [score for score in scores if score is not None]
    return min(known) if known else 1.0


class ChestDetector:
    """Counts a chest only when everything about the attempt fits a finished search.

    An attempt starts when interact is used on the prompt. It counts if the
    prompt then stays gone for CONFIRM_SECONDS and the player kept aiming at the
    chest for the first AIM_SECONDS. If the prompt returns while the player is
    still aiming at the same spot, or they looked away while searching, it was
    cancelled and nothing counts.

    One exception keeps back-to-back chests from being missed: if the prompt comes
    back only after the view has turned to somewhere new, the player has moved on
    to another target, so the finished search still counts.
    """

    PRESS_WINDOW = 0.25  # a press this soon after the prompt still applies to it
    FLICKER_SECONDS = 0.4  # gone for less than this is a detection blip, not a change
    RELEASE_GRACE = 1.0  # prompt still there this long after the key: cancelled
    AIM_SECONDS = 1.0  # must keep looking at the chest this long after pressing
    CONFIRM_SECONDS = 3.0  # prompt must stay gone this long, so a cancel can show itself
    COOLDOWN = 0.5  # a stray press right after counting can't start a new attempt

    def __init__(self):
        self._cooldown_until = 0.0
        self.attempt_id = 0  # counts attempts, so the watcher knows when a new one began
        self.reset()

    def reset(self) -> None:
        self.armed = False
        self._last_seen: float | None = None
        self._last_key = 0.0
        self._pressed_at = 0.0
        self._gone_since: float | None = None
        self._looked_away = False
        self._announced = False

    def _finish(self, event: str) -> str:
        self.armed = False
        self._gone_since = None
        return event

    def update(
        self, now: float, prompt_visible: bool, key_down: bool, looked_away: bool = False
    ) -> str | None:
        """Feed one frame. Returns PENDING, OPENED or CANCELLED when something happens."""
        if prompt_visible:
            self._last_seen = now
        if self._last_seen is None:
            return None

        if (
            key_down
            and now - self._last_seen <= self.PRESS_WINDOW
            and now >= self._cooldown_until
        ):
            if not self.armed:
                self.armed = True
                self.attempt_id += 1
                self._pressed_at = now
                self._looked_away = False
                self._announced = False
            self._last_key = now

        if not self.armed:
            return None

        if looked_away and now - self._pressed_at <= self.AIM_SECONDS:
            self._looked_away = True

        if prompt_visible:
            if self._gone_since is not None and now - self._gone_since >= self.FLICKER_SECONDS:
                # The prompt came back. Same view: the search was stopped. A new view
                # after a full first second of aiming: they finished and moved on.
                moved_on = (
                    looked_away
                    and not self._looked_away
                    and now - self._pressed_at >= self.AIM_SECONDS
                )
                return self._finish(OPENED if moved_on else CANCELLED)
            self._gone_since = None
            if now - self._last_key > self.RELEASE_GRACE:
                return self._finish(CANCELLED)
            return None

        if self._gone_since is None:
            self._gone_since = self._last_seen

        gone_for = now - self._gone_since
        if gone_for >= self.CONFIRM_SECONDS:
            if self._looked_away:
                return self._finish(CANCELLED)
            self._cooldown_until = now + self.COOLDOWN
            return self._finish(OPENED)
        if gone_for >= self.FLICKER_SECONDS and not self._announced:
            self._announced = True
            return PENDING
        return None
