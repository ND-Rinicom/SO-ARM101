#!/usr/bin/env python
"""Simple type definitions for SO-ARM101 robot control."""

from enum import Enum
from typing import Any, TypeAlias

# Robot communication types
RobotAction: TypeAlias = dict[str, Any]
RobotObservation: TypeAlias = dict[str, Any]


class TeleopEvents(Enum):
    """Shared constants for teleoperator events across teleoperators."""

    SUCCESS = "success"
    FAILURE = "failure"
    RERECORD_EPISODE = "rerecord_episode"
    IS_INTERVENTION = "is_intervention"
    TERMINATE_EPISODE = "terminate_episode"
