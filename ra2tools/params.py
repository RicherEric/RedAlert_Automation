"""Tunable assumptions shared by the simulators.

Everything here is an approximation of engine behaviour that is not stated in rulesmd.ini.
These are the values to calibrate against live games.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, replace


@dataclass
class Calibration:
    fps: float = 15.0                   # game logic frames per second (depends on game speed)
    speed_to_cells: float = 0.02        # cells per frame per point of Speed=
    ore_distance: float = 8.0           # cells between refinery and ore field
    harvest_frames_per_bale: float = 3.0
    unload_frames: float = 60.0
    chrono_return_frames: float = 45.0  # chrono miner teleport back
    slave_cycle_frames: float = 300.0   # one slave miner worker round trip
    place_delay_frames: float = 15.0    # time for the player to place a finished building
    mcv_deploy_frames: float = 75.0
    # The same two delays when ra2bridge places/deploys (measured 2026-10-31: ~5 and <=27 frames,
    # polling latency included). The human values above include reaction time.
    bot_place_delay_frames: float = 5.0
    bot_mcv_deploy_frames: float = 27.0
    controller: str = "human"           # "bot" = plan for execute.py (uses the bot_* delays)
    start_credits: int = 10000
    aircraft_reload_frames: float = 450.0  # fly home, rearm, come back
    game_speed: int | None = None       # in-game speed setting; picks fps from fps_by_speed
    fps_by_speed: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict | None) -> "Calibration":
        names = {f.name for f in fields(cls)}
        cal = cls(**{k: v for k, v in (data or {}).items() if k in names})
        if cal.game_speed is not None and str(cal.game_speed) in cal.fps_by_speed:
            cal.fps = float(cal.fps_by_speed[str(cal.game_speed)])
        return cal.as_bot() if cal.controller == "bot" else cal

    def as_bot(self) -> "Calibration":
        """Timings for orders given by ra2bridge instead of a player."""
        return replace(self, controller="bot", place_delay_frames=self.bot_place_delay_frames,
                       mcv_deploy_frames=self.bot_mcv_deploy_frames)

    def frames_to_seconds(self, frames: float, live_fps: float | None = None) -> float:
        """Prefer a live measurement (monitor's LiveFps) over the calibrated value."""
        return frames / (live_fps or self.fps)
