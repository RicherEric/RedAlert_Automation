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
    # Ore depletion: once refinery harvesters have brought in this many credits the home field is
    # empty and they drive to a field ore_far_distance cells out (0 = never runs out). Measured
    # 2026-11-05 from two live games vs hard: 3 HARV switched at ~13100 after ~$15k, cycle 1900 -> 2900.
    ore_home_credits: float = 0.0
    # Share of a full load a refinery harvester brings back per trip on average (partial loads,
    # harassment). Live hard games 2026-11-05: about 0.8 of the cycle-time estimate.
    harvest_efficiency: float = 1.0
    ore_far_distance: float = 0.0
    # Tech oil derricks an engineer can take: walking distance in cells from our base, nearest
    # first (the map has 3; the AI took the one on its side at 18805, live 2026-11-06). Each gives
    # its rules' ProduceCashStartup on capture, then ProduceCashAmount every ProduceCashDelay.
    derricks: list = field(default_factory=list)
    derrick_type: str = "CAOILD"
    slave_cycle_frames: float = 300.0   # one slave miner worker round trip
    place_delay_frames: float = 15.0    # time for the player to place a finished building
    # Whether an Industrial Plant's discount also shortens build time (time comes from the price
    # in BuildSpeed). Not yet measured live: set False if the game keeps the full time.
    plant_speeds_build: bool = True
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
