from __future__ import annotations

from fnmatch import fnmatch


class FakeGroupChatRedis:
    def __init__(self) -> None:
        self._zsets: dict[str, dict[str, float]] = {}
        self._hashes: dict[str, dict[str, str]] = {}
        self._sets: dict[str, set[str]] = {}

    def ping(self) -> bool:
        return True

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self._zsets.setdefault(key, {}).update(mapping)

    def zremrangebyscore(self, key: str, min_score: str | float, max_score: str | float) -> None:
        zset = self._zsets.get(key)
        if not zset:
            return
        max_score_text = str(max_score)
        max_exclusive = max_score_text.startswith('(')
        max_value = float(max_score_text[1:] if max_exclusive else max_score_text)
        to_remove = []
        for member, score in zset.items():
            if score < max_value or (score == max_value and not max_exclusive):
                to_remove.append(member)
        for member in to_remove:
            zset.pop(member, None)

    def zrevrange(self, key: str, start: int, end: int) -> list[str]:
        zset = self._zsets.get(key, {})
        items = [member for member, _ in sorted(zset.items(), key=lambda item: (item[1], item[0]), reverse=True)]
        if end == -1:
            return items[start:]
        return items[start : end + 1]

    def hset(self, key: str, field: str, value: str) -> None:
        self._hashes.setdefault(key, {})[field] = value

    def hsetnx(self, key: str, field: str, value: str) -> int:
        bucket = self._hashes.setdefault(key, {})
        if field in bucket:
            return 0
        bucket[field] = value
        return 1

    def hget(self, key: str, field: str) -> str | None:
        return self._hashes.get(key, {}).get(field)

    def sadd(self, key: str, *values: str) -> None:
        self._sets.setdefault(key, set()).update(values)

    def smembers(self, key: str) -> set[str]:
        return set(self._sets.get(key, set()))

    def scan_iter(self, match: str) -> list[str]:
        keys = set(self._zsets) | set(self._hashes) | set(self._sets)
        return [key for key in keys if fnmatch(key, match)]

    def delete(self, *keys: str) -> None:
        for key in keys:
            self._zsets.pop(key, None)
            self._hashes.pop(key, None)
            self._sets.pop(key, None)
