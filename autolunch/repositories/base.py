"""
AutoLunch — Repository Layer: Base Abstract Repository

The Repository pattern decouples data storage (JSON files today,
could be SQLite or Postgres tomorrow) from business logic.
All repositories implement this interface.
"""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Generic, TypeVar

from loguru import logger
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class BaseRepository(ABC, Generic[T]):
    """
    Abstract base for all AutoLunch repositories.
    Concrete implementations handle JSON file I/O.
    """

    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        logger.debug(f"Repository initialized", repo=self.__class__.__name__, path=str(file_path))

    @abstractmethod
    def load(self) -> T:
        """Load and return the model from storage."""

    @abstractmethod
    def save(self, data: T) -> None:
        """Persist the model to storage."""

    def _ensure_parent(self) -> None:
        """Create parent directories if they don't exist."""
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
