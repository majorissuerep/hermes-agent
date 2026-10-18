"""Transport liveness + build capability probes (``methods_voice.py`` hosts them)."""

from __future__ import annotations

from .base import Params, Result
from .registry import method


class PingParams(Params):
    pass


class PingResult(Result):
    pong: bool


method("ping", params=PingParams, result=PingResult,
       doc="Cheapest liveness probe; answered on the WS reader thread even while every agent is mid-turn.")
