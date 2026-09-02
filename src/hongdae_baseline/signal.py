from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import xml.etree.ElementTree as ET


StateSetter = Callable[[str], None]


@dataclass(frozen=True)
class NativePhase:
    duration: float
    state: str
    min_duration: float | None
    max_duration: float | None


@dataclass(frozen=True)
class TLSDefinition:
    tls_id: str
    native_type: str
    native_phases: tuple[NativePhase, ...]
    green_states: tuple[str, ...]
    incoming_lanes: tuple[str, ...]

    @classmethod
    def from_network(cls, network: Path, tls_id: str) -> "TLSDefinition":
        root = ET.parse(network).getroot()
        logic = next((item for item in root.findall("tlLogic") if item.get("id") == tls_id), None)
        if logic is None:
            raise ValueError(f"TLS {tls_id} not found in {network}")
        native = tuple(
            NativePhase(
                duration=float(phase.get("duration", "0")),
                state=phase.get("state", ""),
                min_duration=float(phase.get("minDur")) if phase.get("minDur") is not None else None,
                max_duration=float(phase.get("maxDur")) if phase.get("maxDur") is not None else None,
            )
            for phase in logic.findall("phase")
        )
        green_states = tuple(
            phase.state
            for phase in native
            if "y" not in phase.state.lower()
            and any(character in "Gg" for character in phase.state)
        )
        connections = sorted(
            (
                int(connection.get("linkIndex", "0")),
                f"{connection.get('from')}_{connection.get('fromLane')}",
            )
            for connection in root.findall("connection")
            if connection.get("tl") == tls_id
        )
        lanes: list[str] = []
        for _, lane in connections:
            if lane not in lanes:
                lanes.append(lane)
        if not green_states or not lanes:
            raise ValueError(f"TLS {tls_id} is missing green phases or controlled lanes")
        return cls(tls_id, logic.get("type", "unknown"), native, green_states, tuple(lanes))

    @property
    def native_yellow_durations(self) -> tuple[float, ...]:
        return tuple(phase.duration for phase in self.native_phases if "y" in phase.state.lower())


def transition_yellow_state(old_state: str, new_state: str) -> str:
    if len(old_state) != len(new_state):
        raise ValueError("Signal states have different lengths")
    return "".join(
        "y" if old in "Gg" and new in "rs" else old
        for old, new in zip(old_state, new_state, strict=True)
    )


class PPOPhaseController:
    """Explicit phase scheduler for legacy-compatible and corrected PPO execution."""

    def __init__(
        self,
        green_states: tuple[str, ...],
        min_green: int,
        yellow_time: int,
        max_green: int,
        enforce_max_green: bool,
    ) -> None:
        if len(green_states) < 2:
            raise ValueError("At least two green phases are required")
        self.green_states = green_states
        self.min_green = min_green
        self.yellow_time = yellow_time
        self.max_green = max_green
        self.enforce_max_green = enforce_max_green
        self.green_phase = 0
        self.pending_phase: int | None = None
        self.time_since_last_phase_change = 0
        self.green_elapsed = 0
        self.yellow_elapsed = 0
        self.is_yellow = False
        self.forced_transitions = 0

    @property
    def observation_phase(self) -> int:
        # sumo-rl updates green_phase to the target as soon as yellow starts.
        return self.pending_phase if self.pending_phase is not None else self.green_phase

    def initialize(self, set_state: StateSetter) -> None:
        set_state(self.green_states[0])

    def request_phase(self, new_phase: int, set_state: StateSetter) -> bool:
        if not 0 <= int(new_phase) < len(self.green_states):
            raise ValueError(f"Phase {new_phase} is outside action space")
        new_phase = int(new_phase)
        if not self.enforce_max_green:
            return self._legacy_request(new_phase, set_state)
        if self.pending_phase is not None or new_phase == self.green_phase or self.green_elapsed < self.min_green:
            return False
        self._start_corrected_transition(new_phase, set_state, forced=False)
        return True

    def tick(self, set_state: StateSetter) -> None:
        if not self.enforce_max_green:
            self._legacy_tick(set_state)
            return
        if self.pending_phase is not None:
            self.yellow_elapsed += 1
            if self.yellow_elapsed >= self.yellow_time:
                self.green_phase = self.pending_phase
                self.pending_phase = None
                self.is_yellow = False
                self.green_elapsed = 0
                self.time_since_last_phase_change = 0
                set_state(self.green_states[self.green_phase])
            return
        self.green_elapsed += 1
        self.time_since_last_phase_change += 1
        if self.green_elapsed >= self.max_green:
            next_phase = (self.green_phase + 1) % len(self.green_states)
            self._start_corrected_transition(next_phase, set_state, forced=True)

    def _legacy_request(self, new_phase: int, set_state: StateSetter) -> bool:
        if (
            self.green_phase == new_phase
            or self.time_since_last_phase_change < self.yellow_time + self.min_green
        ):
            set_state(self.green_states[self.green_phase])
            return False
        old_phase = self.green_phase
        set_state(transition_yellow_state(self.green_states[old_phase], self.green_states[new_phase]))
        self.green_phase = new_phase
        self.pending_phase = new_phase
        self.is_yellow = True
        self.time_since_last_phase_change = 0
        self.yellow_elapsed = 0
        return True

    def _legacy_tick(self, set_state: StateSetter) -> None:
        self.time_since_last_phase_change += 1
        if self.is_yellow:
            self.yellow_elapsed += 1
            if self.yellow_elapsed >= self.yellow_time:
                set_state(self.green_states[self.green_phase])
                self.is_yellow = False
                self.pending_phase = None

    def _start_corrected_transition(self, new_phase: int, set_state: StateSetter, forced: bool) -> None:
        set_state(transition_yellow_state(self.green_states[self.green_phase], self.green_states[new_phase]))
        self.pending_phase = new_phase
        self.is_yellow = True
        self.yellow_elapsed = 0
        self.time_since_last_phase_change = 0
        if forced:
            self.forced_transitions += 1

