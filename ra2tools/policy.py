"""Trained build policy (stage 3): LightGBM over BuildEnv features, masked to legal actions.

The policy answers a decision in about a millisecond, versus seconds for the GA teacher, which
is what makes it usable in a live game. A value model predicts the teacher plan's finish frame
and spending from a state, for comparing options under different objective weights.

Safety net (`unstick`): a student that keeps answering WAIT while the whole base sits idle would
stay stuck until the time limit (e.g. never building the radar a Flak Trooper needs). Then the
best-scoring legal item on the remaining targets' prerequisite chain is built instead.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .env import FEATURE_VERSION, WAIT, BuildEnv, Observation, features, needed_items

MODELS = Path(__file__).resolve().parent.parent / "models"
IDLE_FRAMES = 90  # whole base idle this long while the student still waits -> unstick


def unstick(env: BuildEnv, obs: Observation, scores: np.ndarray, idle_frames: int = IDLE_FRAMES) -> int | None:
    """Action to play instead of WAIT when the base has been idle too long, else None."""
    s = obs.state
    if any(s.jobs.values()):
        return None
    last = max((f for f, _, _ in s.events), default=0)
    if s.frame - last < idle_frames:
        return None
    need = needed_items(env, obs)
    options = [i for i in np.flatnonzero(obs.mask) if i != WAIT and env.vocab[i - 1] in need]
    if not options:
        return None
    return int(max(options, key=lambda i: scores[i]))


class BuildPolicy:
    def __init__(self, directory: Path = MODELS / "build", safety_net: bool = True):
        import lightgbm as lgb

        self.safety_net = safety_net
        self.unstuck = 0  # times the safety net replaced a WAIT

        meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
        if meta.get("feature_version", 1) != FEATURE_VERSION:
            raise ValueError(f"{directory} was trained on feature version {meta.get('feature_version', 1)}, "
                             f"but ra2tools.env is at version {FEATURE_VERSION}: retrain with train.py")
        self.vocab: list[str] = meta["vocab"]
        self.classes = np.array(meta["classes"])  # model output column -> env action index
        self.policy = lgb.Booster(model_file=str(directory / "policy.txt"))
        value = directory / "value_finish.txt"
        self.value_finish = lgb.Booster(model_file=str(value)) if value.exists() else None

    def scores(self, x: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """Probability per env action index (illegal ones 0)."""
        p = self.policy.predict(x[None, :], num_threads=1)[0]
        out = np.zeros(mask.shape[0])
        out[self.classes] = p
        out[~mask] = 0
        return out

    def act(self, env: BuildEnv, obs: Observation, weights: dict[str, float] | None = None) -> int:
        if env.vocab != self.vocab:
            raise ValueError("environment vocabulary differs from the one the policy was trained on")
        s = self.scores(features(env, obs, weights), obs.mask)
        if s.max() <= 0:  # every legal action unseen in training: fall back to the first legal one
            return int(np.flatnonzero(obs.mask)[0])
        a = int(np.argmax(s))
        if a == WAIT and self.safety_net:
            fix = unstick(env, obs, s)
            if fix is not None:
                self.unstuck += 1
                return fix
        return a

    def predicted_finish(self, env: BuildEnv, obs: Observation, weights=None) -> float | None:
        if self.value_finish is None:
            return None
        togo = self.value_finish.predict(features(env, obs, weights)[None, :], num_threads=1)[0]
        return obs.state.frame + float(togo)
