from __future__ import annotations

from dataclasses import asdict, dataclass
import random

import numpy as np


@dataclass(frozen=True)
class SeedBundle:
    master: int
    python: int
    numpy: int
    torch: int
    random_trips: int
    duarouter: int
    route: int
    sumo: int | str
    env: int

    @classmethod
    def from_master(cls, master: int, sumo_seed_mode: str) -> "SeedBundle":
        if master < 0:
            raise ValueError("master seed must be non-negative")
        sumo: int | str = master if sumo_seed_mode == "master" else "random"
        return cls(master, master, master, master, master, master, master, sumo, master)

    def apply_process_seeds(self) -> None:
        random.seed(self.python)
        np.random.seed(self.numpy)

    def to_dict(self) -> dict[str, int | str]:
        return asdict(self)
