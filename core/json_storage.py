from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

try:  # Windows
    import msvcrt
except ImportError:  # POSIX, в том числе среда тестирования
    msvcrt = None
    import fcntl

from config import DATA_DIR, INITIAL_JSON_DATA, IMPORTANT_JSON_KEYS, JSON_FILES

_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}


class JsonStorageError(RuntimeError):
    print()


class CorruptJsonError(JsonStorageError):
    print()

def json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Тип {type(value).__name__} нельзя сериализовать в JSON")


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve()).casefold()
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    lock = _thread_lock(path)
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock:
        with lock_path.open("a+b") as handle:
            if msvcrt is not None:
                handle.seek(0)
                if handle.read(1) == b"":
                    handle.seek(0)
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                while True:
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                        break
                    except OSError:
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class JsonStorage:

    def __init__(self, data_dir: Path | str | None = None):
        self.data_dir = Path(data_dir) if data_dir is not None else DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.paths = {
            key: (self.data_dir / path.name) if data_dir is not None else path
            for key, path in JSON_FILES.items()
        }
        self.initial_data = INITIAL_JSON_DATA
        self._initialize_missing_files()

    def _initialize_missing_files(self) -> None:
        for key, path in self.paths.items():
            if not path.exists():
                self.save(key, self.initial_data[key], backup=False)

    def _parse(self, path: Path) -> Any:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file, parse_float=Decimal, parse_int=int)

    def _quarantine(self, path: Path) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = path.with_name(f"{path.name}.corrupt_{stamp}_{uuid.uuid4().hex[:6]}")
        os.replace(path, target)
        return target

    def load(self, key: str) -> Any:
        path = self._path_for(key)
        with _file_lock(path):
            if not path.exists():
                self._write_atomic(path, self.initial_data[key])
                return self.initial_data[key].copy()
            try:
                return self._parse(path)
            except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
                damaged_path = self._quarantine(path)
                backup = path.with_suffix(path.suffix + ".bak")
                if backup.exists():
                    try:
                        recovered = self._parse(backup)
                        self._write_atomic(path, recovered)
                        raise JsonStorageError(
                            f"Файл {path.name} повреждён и сохранён как {damaged_path.name}; "
                            "состояние восстановлено из резервной копии. Проверьте диагностику."
                        ) from exc
                    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as backup_exc:
                        raise CorruptJsonError(
                            f"Повреждены {path.name} и резервная копия {backup.name}. "
                            f"Исходник сохранён: {damaged_path}"
                        ) from backup_exc
                raise CorruptJsonError(
                    f"JSON {path.name} повреждён; файл сохранён как {damaged_path.name}. "
                    "Автоматически пустыми данными он не заменён."
                ) from exc

    def save(self, key: str, data: Any, *, backup: bool | None = None) -> None:
        path = self._path_for(key)
        use_backup = key in IMPORTANT_JSON_KEYS if backup is None else backup
        with _file_lock(path):
            if use_backup and path.exists():
                # Копируем только валидный JSON; повреждённый файл не становится backup.
                try:
                    self._parse(path)
                except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
                    raise CorruptJsonError(
                        f"Отказ перезаписи повреждённого файла {path.name}; сначала восстановите его."
                    ) from exc
                backup_path = path.with_suffix(path.suffix + ".bak")
                backup_tmp = backup_path.with_suffix(backup_path.suffix + ".tmp")
                with path.open("rb") as src, backup_tmp.open("wb") as dst:
                    while chunk := src.read(1024 * 1024):
                        dst.write(chunk)
                    dst.flush()
                    os.fsync(dst.fileno())
                os.replace(backup_tmp, backup_path)
            self._write_atomic(path, data)

    def _write_atomic(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as file:
                json.dump(data, file, ensure_ascii=False, indent=2, default=json_default, allow_nan=False)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp, path)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def _path_for(self, key: str) -> Path:
        try:
            return self.paths[key]
        except KeyError as exc:
            raise KeyError(f"Неизвестный JSON-раздел {key!r}. Доступны: {', '.join(self.paths)}") from exc
