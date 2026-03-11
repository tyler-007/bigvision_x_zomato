"""
AutoLunch — Repository: Agent Memory
Reads/writes data/memory.json — the agent's append-only episodic log.
"""
import json
from pathlib import Path

from loguru import logger

from autolunch.models.memory import AgentMemory, PastOrder, Rejection, LearnedBlock
from autolunch.repositories.base import BaseRepository


class MemoryRepository(BaseRepository[AgentMemory]):
    """
    Manages the agent's episodic memory (data/memory.json).
    Loads the full log on startup; provides targeted append methods
    for orders, rejections, and learned blocks.
    """

    def load(self) -> AgentMemory:
        """
        Load memory from disk. Returns empty AgentMemory if file doesn't
        exist yet (first run).
        """
        if not self._file_path.exists():
            logger.info("No memory file found — starting fresh", path=str(self._file_path))
            return AgentMemory()

        try:
            raw = json.loads(self._file_path.read_text(encoding="utf-8"))
            memory = AgentMemory.model_validate(raw)
            logger.info(
                "Memory loaded",
                total_orders=len(memory.past_orders),
                total_rejections=len(memory.rejections),
                learned_blocks=len(memory.learned_blocks),
            )
            return memory
        except Exception as e:
            logger.error(f"Memory file corrupted, starting fresh: {e}")
            return AgentMemory()  # Soft fail — don't crash the workflow

    def save(self, data: AgentMemory) -> None:
        """Persist the full memory object to disk."""
        self._ensure_parent()
        self._file_path.write_text(
            data.model_dump_json(indent=2),
            encoding="utf-8",
        )
        logger.debug("Memory saved", path=str(self._file_path))

    # ── Convenience append methods ────────────────────────────────────────────

    def append_order(self, order: PastOrder) -> None:
        """Append a new order and persist immediately."""
        memory = self.load()
        memory.past_orders.append(order)
        self.save(memory)
        logger.info("Order logged to memory", restaurant=order.restaurant_name, item=order.item_name)

    def append_rejection(self, rejection: Rejection) -> None:
        """Append a rejection and persist immediately."""
        memory = self.load()
        memory.rejections.append(rejection)
        self.save(memory)
        logger.info(
            "Rejection logged to memory",
            restaurant=rejection.suggested_restaurant,
            reason=rejection.user_reason,
        )

    def append_learned_block(self, block: LearnedBlock) -> None:
        """Append a new learned block (auto-derived from repeated rejections)."""
        memory = self.load()
        memory.learned_blocks.append(block)
        self.save(memory)
        logger.info("Learned block added", entity=block.blocked_entity, type=block.block_type)


def get_memory_repository(data_dir: Path) -> MemoryRepository:
    """Factory function."""
    return MemoryRepository(data_dir / "memory.json")
