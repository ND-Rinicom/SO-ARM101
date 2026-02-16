# Copyright 2024 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import abc
import builtins
import logging
from pathlib import Path
from pprint import pformat
from typing import Union

from lerobot.motors import MotorCalibration
from lerobot.types import RobotAction, RobotObservation
from lerobot.utils.constants import HF_LEROBOT_CALIBRATION, ROBOTS

from .config import RobotConfig

logger = logging.getLogger(__name__)


def ensure_safe_goal_position(
    goal_present_pos: dict[str, tuple[float, float]], max_relative_target: float | dict[str, float]
) -> dict[str, float]:
    """Caps relative action target magnitude for safety."""

    if isinstance(max_relative_target, float):
        diff_cap = dict.fromkeys(goal_present_pos, max_relative_target)
    elif isinstance(max_relative_target, dict):
        if not set(goal_present_pos) == set(max_relative_target):
            raise ValueError("max_relative_target keys must match those of goal_present_pos.")
        diff_cap = max_relative_target
    else:
        raise TypeError(max_relative_target)

    warnings_dict = {}
    safe_goal_positions = {}
    for key, (goal_pos, present_pos) in goal_present_pos.items():
        diff = goal_pos - present_pos
        max_diff = diff_cap[key]
        safe_diff = min(diff, max_diff)
        safe_diff = max(safe_diff, -max_diff)
        safe_goal_pos = present_pos + safe_diff
        safe_goal_positions[key] = safe_goal_pos
        if abs(safe_goal_pos - goal_pos) > 1e-4:
            warnings_dict[key] = {
                "original goal_pos": goal_pos,
                "safe goal_pos": safe_goal_pos,
            }

    if warnings_dict:
        logging.warning(
            "Relative goal position magnitude had to be clamped to be safe.\n"
            f"{pformat(warnings_dict, indent=4)}"
        )

    return safe_goal_positions


# TODO(aliberts): action/obs typing such as Generic[ObsType, ActType] similar to gym.Env ?
# https://github.com/Farama-Foundation/Gymnasium/blob/3287c869f9a48d99454306b0d4b4ec537f0f35e3/gymnasium/core.py#L23
class Robot(abc.ABC):
    """
    The base abstract class for all LeRobot-compatible robots.

    This class provides a standardized interface for interacting with physical robots.
    Subclasses must implement all abstract methods and properties to be usable.

    Attributes:
        config_class (RobotConfig): The expected configuration class for this robot.
        name (str): The unique robot name used to identify this robot type.
    """

    # Set these in ALL subclasses
    config_class: builtins.type[RobotConfig]
    name: str

    def __init__(self, config: RobotConfig):
        self.robot_type = self.name
        self.id = config.id
        self.calibration_dir = (
            config.calibration_dir if config.calibration_dir else HF_LEROBOT_CALIBRATION / ROBOTS / self.name
        )
        self.calibration_dir.mkdir(parents=True, exist_ok=True)
        self.calibration_fpath = self.calibration_dir / f"{self.id}.json"
        self.calibration: dict[str, MotorCalibration] = {}
        if self.calibration_fpath.is_file():
            self._load_calibration()

    def __str__(self) -> str:
        return f"{self.id} {self.__class__.__name__}"

    def __enter__(self):
        """
        Context manager entry.
        Automatically connects to the camera.
        """
        self.connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """
        Context manager exit.
        Automatically disconnects, ensuring resources are released even on error.
        """
        self.disconnect()

    def __del__(self) -> None:
        """
        Destructor safety net.
        Attempts to disconnect if the object is garbage collected without cleanup.
        """
        try:
            if self.is_connected:
                self.disconnect()
        except Exception:  # nosec B110
            pass

    # TODO(aliberts): create a proper Feature class for this that links with datasets
    @property
    @abc.abstractmethod
    def observation_features(self) -> dict:
        """
        A dictionary describing the structure and types of the observations produced by the robot.
        Its structure (keys) should match the structure of what is returned by :pymeth:`get_observation`.
        Values for the dict should either be:
            - The type of the value if it's a simple value, e.g. `float` for single proprioceptive value (a joint's position/velocity)
            - A tuple representing the shape if it's an array-type value, e.g. `(height, width, channel)` for images

        Note: this property should be able to be called regardless of whether the robot is connected or not.
        """
        pass

    @property
    @abc.abstractmethod
    def action_features(self) -> dict:
        """
        A dictionary describing the structure and types of the actions expected by the robot. Its structure
        (keys) should match the structure of what is passed to :pymeth:`send_action`. Values for the dict
        should be the type of the value if it's a simple value, e.g. `float` for single proprioceptive value
        (a joint's goal position/velocity)

        Note: this property should be able to be called regardless of whether the robot is connected or not.
        """
        pass

    @property
    @abc.abstractmethod
    def is_connected(self) -> bool:
        """
        Whether the robot is currently connected or not. If `False`, calling :pymeth:`get_observation` or
        :pymeth:`send_action` should raise an error.
        """
        pass

    @abc.abstractmethod
    def connect(self, calibrate: bool = True) -> None:
        """
        Establish communication with the robot.

        Args:
            calibrate (bool): If True, automatically calibrate the robot after connecting if it's not
                calibrated or needs calibration (this is hardware-dependant).
        """
        pass

    @property
    @abc.abstractmethod
    def is_calibrated(self) -> bool:
        """Whether the robot is currently calibrated or not. Should be always `True` if not applicable"""
        pass

    @abc.abstractmethod
    def calibrate(self) -> None:
        """
        Calibrate the robot if applicable. If not, this should be a no-op.

        This method should collect any necessary data (e.g., motor offsets) and update the
        :pyattr:`calibration` dictionary accordingly.
        """
        pass

    def _load_calibration(self, fpath: Union[Path, None] = None) -> None:
        """
        Helper to load calibration data from the specified file.

        Args:
            fpath (Union[Path, None]): Optional path to the calibration file. Defaults to `self.calibration_fpath`.
        """
        import json
        
        fpath = self.calibration_fpath if fpath is None else fpath
        
        # Check if file exists and is not empty
        if not fpath.exists() or fpath.stat().st_size == 0:
            return  # No calibration file or empty file, will be created during calibration
        
        try:
            with open(fpath) as f:
                # Load JSON directly and convert to MotorCalibration objects
                calibration_data = json.load(f)
                self.calibration = {}
                for motor_name, motor_dict in calibration_data.items():
                    self.calibration[motor_name] = MotorCalibration(**motor_dict)
        except (json.JSONDecodeError, ValueError) as e:
            # Corrupted calibration file, ignore and will be recreated
            logging.warning(f"Corrupted calibration file at {fpath}, will be recreated: {e}")
            return

    def _save_calibration(self, fpath: Union[Path, None] = None) -> None:
        """
        Helper to save calibration data to the specified file.

        Args:
            fpath (Union[Path, None]): Optional path to save the calibration file. Defaults to `self.calibration_fpath`.
        """
        import json
        from dataclasses import asdict
        
        fpath = self.calibration_fpath if fpath is None else fpath
        
        # Convert calibration dict to JSON-serializable format
        calibration_data = {}
        for motor_name, motor_cal in self.calibration.items():
            calibration_data[motor_name] = asdict(motor_cal)
        
        # Save as JSON directly (bypasses draccus Python 3.13 bug)
        with open(fpath, "w") as f:
            json.dump(calibration_data, f, indent=4)

    @abc.abstractmethod
    def configure(self) -> None:
        """
        Apply any one-time or runtime configuration to the robot.
        This may include setting motor parameters, control modes, or initial state.
        """
        pass

    @abc.abstractmethod
    def get_observation(self) -> RobotObservation:
        """
        Retrieve the current observation from the robot.

        Returns:
            RobotObservation: A flat dictionary representing the robot's current sensory state. Its structure
                should match :pymeth:`observation_features`.
        """

        pass

    @abc.abstractmethod
    def send_action(self, action: RobotAction) -> RobotAction:
        """
        Send an action command to the robot.

        Args:
            action (RobotAction): Dictionary representing the desired action. Its structure should match
                :pymeth:`action_features`.

        Returns:
            RobotAction: The action actually sent to the motors potentially clipped or modified, e.g. by
                safety limits on velocity.
        """
        pass

    @abc.abstractmethod
    def disconnect(self) -> None:
        """Disconnect from the robot and perform any necessary cleanup."""
        pass
