"""原子 JSON 读写工具模块。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class JSON_IO:
    """原子 JSON 读写工具。

    所有写入操作通过 tmp 文件 → fsync → os.replace 实现原子性，
    确保写入过程中断不会损坏已有数据。
    """

    @staticmethod
    def save(path: Path, data: Any) -> None:
        """原子写入 JSON 文件。

        流程：写入同目录临时文件 → fsync 刷盘 → os.replace 原子替换。
        异常时清理临时文件并重新抛出。
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        fd, tmp_path = tempfile.mkstemp(
            dir=path.parent, suffix=".tmp", prefix=".json_io_"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            # 清理临时文件（可能已被 replace 移走，忽略 FileNotFoundError）
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def load(path: Path) -> Any | None:
        """读取 JSON 文件。文件不存在返回 None。"""
        path = Path(path)
        if not path.exists():
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def append_to_list(path: Path, item: Any) -> None:
        """读取现有 JSON 列表 → 追加新条目 → 原子写回。

        文件不存在时视为空列表。
        """
        path = Path(path)
        existing = JSON_IO.load(path)
        if existing is None:
            existing = []
        existing.append(item)
        JSON_IO.save(path, existing)
