"""Kill Switch configuration loader.

P0-1: Paid Pilot Scorecard & Kill Switch

Loads kill switch mode from:
1. Environment variable: KILL_SWITCH_MODE (override)
2. config/kill_switch.yaml (default)

Modes:
- NORMAL: All features enabled
- SAFE_MODE: Restricts high-risk operations
- HARD_STOP: Emergency shutdown
"""

import logging
import os
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class KillSwitchMode(str, Enum):
    """Kill switch operational modes."""

    NORMAL = "NORMAL"
    SAFE_MODE = "SAFE_MODE"
    HARD_STOP = "HARD_STOP"


class KillSwitchState(BaseModel):
    """Kill switch state model."""

    mode: KillSwitchMode = Field(
        default=KillSwitchMode.NORMAL,
        description="Current kill switch mode",
    )
    reason: str = Field(
        default="Default configuration",
        description="Reason for current mode",
    )
    set_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when mode was set (UTC)",
    )
    set_by_ip: Optional[str] = Field(
        default=None,
        description="IP address of actor who set the mode",
    )
    ttl_minutes: int = Field(
        default=0,
        description="Auto-restore to NORMAL after N minutes (0 = no auto-restore)",
    )
    expires_at: Optional[datetime] = Field(
        default=None,
        description="Expiration timestamp for auto-restore (UTC)",
    )

    def is_expired(self) -> bool:
        """Check if TTL has expired.

        Returns:
            True if expired (should auto-restore to NORMAL), False otherwise
        """
        if self.ttl_minutes == 0 or self.expires_at is None:
            return False

        now = datetime.now(timezone.utc)
        return now >= self.expires_at

    def to_kst_display(self) -> dict:
        """Convert timestamps to KST for display.

        Returns:
            Dictionary with KST-formatted timestamps
        """
        from datetime import timedelta

        kst_offset = timedelta(hours=9)

        result = self.model_dump(exclude_none=True)

        if self.set_at:
            kst_time = self.set_at + kst_offset
            result["set_at"] = kst_time.isoformat()

        if self.expires_at:
            kst_time = self.expires_at + kst_offset
            result["expires_at"] = kst_time.isoformat()

        return result


class KillSwitchConfig:
    """Kill switch configuration manager.

    Singleton pattern for global state management.
    """

    _instance: Optional["KillSwitchConfig"] = None
    _state: KillSwitchState

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._state = cls._load_initial_state()
        return cls._instance

    @classmethod
    def _load_initial_state(cls) -> KillSwitchState:
        """Load initial kill switch state from config or env.

        Priority:
        1. Environment variable: KILL_SWITCH_MODE
        2. config/kill_switch.yaml
        3. Default: NORMAL

        Returns:
            Initial kill switch state
        """
        # Check environment variable first (override)
        env_mode = os.getenv("KILL_SWITCH_MODE", "").upper()
        if env_mode:
            try:
                mode = KillSwitchMode(env_mode)
                logger.info(
                    f"Kill switch mode loaded from environment: {mode.value}",
                    extra={"event": "kill_switch.loaded_from_env", "mode": mode.value},
                )
                return KillSwitchState(
                    mode=mode,
                    reason="Loaded from KILL_SWITCH_MODE environment variable",
                )
            except ValueError:
                logger.warning(
                    f"Invalid KILL_SWITCH_MODE in environment: {env_mode}. Using default.",
                    extra={"event": "kill_switch.invalid_env_mode", "mode": env_mode},
                )

        # Try loading from config file
        config_path = Path(__file__).parent.parent.parent.parent.parent / "config" / "kill_switch.yaml"

        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config_data = yaml.safe_load(f)

                mode_str = config_data.get("mode", "NORMAL").upper()
                mode = KillSwitchMode(mode_str)

                logger.info(
                    f"Kill switch mode loaded from config: {mode.value}",
                    extra={"event": "kill_switch.loaded_from_config", "mode": mode.value},
                )

                return KillSwitchState(
                    mode=mode,
                    reason=config_data.get("reason", "Loaded from config file"),
                )
            except Exception as e:
                logger.error(
                    f"Failed to load kill_switch.yaml: {e}. Using default.",
                    extra={"event": "kill_switch.config_load_error", "error": str(e)},
                )

        # Default fallback
        logger.info(
            "Kill switch mode using default: NORMAL",
            extra={"event": "kill_switch.default_mode"},
        )
        return KillSwitchState(mode=KillSwitchMode.NORMAL)

    def get_state(self) -> KillSwitchState:
        """Get current kill switch state.

        Checks for TTL expiration and auto-restores to NORMAL if expired.

        Returns:
            Current kill switch state
        """
        # Check TTL expiration
        if self._state.is_expired():
            logger.info(
                "Kill switch TTL expired, auto-restoring to NORMAL",
                extra={
                    "event": "kill_switch.ttl_expired",
                    "mode_from": self._state.mode.value,
                    "mode_to": "NORMAL",
                },
            )
            self._state = KillSwitchState(
                mode=KillSwitchMode.NORMAL,
                reason="Auto-restored after TTL expiration",
                set_at=datetime.now(timezone.utc),
            )

        return self._state

    def set_state(
        self,
        mode: KillSwitchMode,
        reason: str,
        actor_ip: str,
        ttl_minutes: int = 0,
    ) -> KillSwitchState:
        """Set kill switch state.

        Args:
            mode: Target kill switch mode
            reason: Explanation for mode change
            actor_ip: IP address of actor making the change
            ttl_minutes: Auto-restore to NORMAL after N minutes (0 = no auto-restore)

        Returns:
            Updated kill switch state

        Raises:
            ValueError: If ttl_minutes is negative
        """
        if ttl_minutes < 0:
            raise ValueError("ttl_minutes must be non-negative")

        # HARD_STOP cannot have TTL (requires manual intervention)
        if mode == KillSwitchMode.HARD_STOP and ttl_minutes > 0:
            logger.warning(
                "HARD_STOP cannot have TTL, ignoring ttl_minutes",
                extra={"event": "kill_switch.hard_stop_ttl_ignored"},
            )
            ttl_minutes = 0

        now = datetime.now(timezone.utc)
        expires_at = None

        if ttl_minutes > 0:
            from datetime import timedelta

            expires_at = now + timedelta(minutes=ttl_minutes)

        old_mode = self._state.mode

        self._state = KillSwitchState(
            mode=mode,
            reason=reason,
            set_at=now,
            set_by_ip=actor_ip,
            ttl_minutes=ttl_minutes,
            expires_at=expires_at,
        )

        logger.info(
            f"Kill switch mode changed: {old_mode.value} -> {mode.value}",
            extra={
                "event": "kill_switch.mode_changed",
                "mode_from": old_mode.value,
                "mode_to": mode.value,
                "reason": reason,
                "actor_ip": actor_ip,
                "ttl_minutes": ttl_minutes,
            },
        )

        return self._state


# Global singleton instance
_kill_switch_config: Optional[KillSwitchConfig] = None


def get_kill_switch_config() -> KillSwitchConfig:
    """Get global kill switch configuration instance.

    Returns:
        Kill switch configuration singleton
    """
    global _kill_switch_config
    if _kill_switch_config is None:
        _kill_switch_config = KillSwitchConfig()
    return _kill_switch_config


def get_current_mode() -> KillSwitchMode:
    """Get current kill switch mode (convenience function).

    Returns:
        Current kill switch mode
    """
    return get_kill_switch_config().get_state().mode
