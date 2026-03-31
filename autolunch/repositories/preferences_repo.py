"""
AutoLunch — Repository: User Preferences
Reads/writes data/preferences.json with Pydantic validation on load.
"""
import json
from pathlib import Path

from loguru import logger

from autolunch.core.exceptions import ConfigurationError
from autolunch.models.preferences import UserPreferences
from autolunch.repositories.base import BaseRepository


class PreferencesRepository(BaseRepository[UserPreferences]):
    """
    Manages the user's preference profile (data/preferences.json).
    Validates the JSON against UserPreferences schema on every load —
    so a corrupted file fails loudly rather than silently producing bad orders.
    """

    def load(self) -> UserPreferences:
        """
        Load and validate user preferences from disk.
        Raises ConfigurationError if the file is missing or malformed.
        """
        if not self._file_path.exists():
            raise ConfigurationError(
                f"Preferences file not found: {self._file_path}. "
                "Please copy data/preferences.json from the example and edit your settings.",
                context={"path": str(self._file_path)},
            )

        try:
            raw = json.loads(self._file_path.read_text(encoding="utf-8"))
            prefs = UserPreferences.model_validate(raw)
            logger.info("Preferences loaded successfully", path=str(self._file_path))
            return prefs
        except json.JSONDecodeError as e:
            raise ConfigurationError(
                f"preferences.json is not valid JSON: {e}",
                context={"path": str(self._file_path)},
            ) from e
        except Exception as e:
            raise ConfigurationError(
                f"Failed to parse preferences: {e}",
                context={"path": str(self._file_path)},
            ) from e

    def save(self, data: UserPreferences) -> None:
        """Write preferences to disk (used during setup/update)."""
        self._ensure_parent()
        self._file_path.write_text(
            data.model_dump_json(indent=2),
            encoding="utf-8",
        )
        logger.info("Preferences saved", path=str(self._file_path))


def get_preferences_repository(data_dir: Path) -> PreferencesRepository:
    """Factory function — use this instead of instantiating directly."""
    return PreferencesRepository(data_dir / "preferences.json")
