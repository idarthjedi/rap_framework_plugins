"""macOS notifications for RAP Importer."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from .logging_config import get_logger

if TYPE_CHECKING:
    from .config import NotificationsConfig

logger = get_logger("notifications")

# Global config reference (set by setup_notifications)
_config: NotificationsConfig | None = None


def setup_notifications(config: NotificationsConfig) -> None:
    """Initialize the notifications module with configuration.

    Args:
        config: Notifications configuration
    """
    global _config
    _config = config
    logger.debug(f"Notifications configured: enabled={config.enabled}, on_error={config.on_error}")


def notify(title: str, message: str, sound: bool = False) -> bool:
    """Show a macOS notification.

    Args:
        title: Notification title
        message: Notification body text
        sound: Whether to play a sound

    Returns:
        True if notification was shown successfully
    """
    if _config is None:
        logger.warning(f"Cannot show notification (config not initialized): {title}")
        return False
    if not _config.enabled:
        logger.debug(f"Notifications disabled, skipping: {title}")
        return False

    try:
        # Build the AppleScript command
        script = f'display notification "{_escape(message)}" with title "{_escape(title)}"'
        if sound:
            script += ' sound name "default"'

        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            check=True,
            timeout=5,
        )
        logger.debug(f"Notification shown: {title}")
        return True

    except subprocess.TimeoutExpired:
        logger.warning(f"Notification timed out: {title}")
        return False
    except subprocess.CalledProcessError as e:
        logger.warning(f"Failed to show notification: {e.stderr.decode() if e.stderr else str(e)}")
        return False
    except Exception as e:
        logger.warning(f"Unexpected notification error: {e}")
        return False


def notify_error(title: str, message: str) -> bool:
    """Show an error notification (if enabled in config).

    Args:
        title: Notification title
        message: Error description

    Returns:
        True if notification was shown
    """
    if _config is None:
        logger.warning(f"Cannot show error notification (config not initialized): {title}")
        return False
    if not _config.on_error:
        logger.debug(f"Error notifications disabled, skipping: {title}")
        return False
    return notify(title, message, sound=True)


def notify_success(title: str, message: str) -> bool:
    """Show a success notification (if enabled in config).

    Args:
        title: Notification title
        message: Success description

    Returns:
        True if notification was shown
    """
    if _config is None or not _config.on_success:
        return False
    return notify(title, message, sound=False)


def _escape(text: str) -> str:
    """Escape text for use in AppleScript string.

    Args:
        text: Text to escape

    Returns:
        Escaped text safe for AppleScript
    """
    # Escape backslashes first, then quotes
    return text.replace("\\", "\\\\").replace('"', '\\"')
