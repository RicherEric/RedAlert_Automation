"""Lenient INI parser matching how RA2/YR reads its rules files."""
from __future__ import annotations


class Ini:
    """Sections and keys keep their original spelling; later duplicates override earlier ones."""

    def __init__(self, text: str):
        self.sections: dict[str, dict[str, str]] = {}
        current: dict[str, str] | None = None
        for raw in text.splitlines():
            line = raw.split(";", 1)[0].strip()
            if not line:
                continue
            if line.startswith("["):
                end = line.find("]")
                if end > 0:
                    name = line[1:end].strip()
                    current = self.sections.setdefault(name, {})
                continue
            if current is None or "=" not in line:
                continue
            key, value = line.split("=", 1)
            current[key.strip()] = value.strip()

    @classmethod
    def from_bytes(cls, data: bytes) -> "Ini":
        return cls(data.decode("latin-1"))

    def __contains__(self, section: str) -> bool:
        return section in self.sections

    def section(self, name: str) -> dict[str, str]:
        return self.sections.get(name, {})

    def get(self, section: str, key: str, default: str | None = None) -> str | None:
        return self.sections.get(section, {}).get(key, default)

    def get_int(self, section: str, key: str, default: int = 0) -> int:
        value = self.get(section, key)
        try:
            return int(float(value)) if value else default
        except ValueError:
            return default

    def get_float(self, section: str, key: str, default: float = 0.0) -> float:
        value = self.get(section, key)
        if not value:
            return default
        try:
            return float(value.rstrip("%")) / 100 if value.endswith("%") else float(value)
        except ValueError:
            return default

    def get_list(self, section: str, key: str) -> list[str]:
        value = self.get(section, key)
        return [v.strip() for v in value.split(",") if v.strip()] if value else []

    def registry(self, section: str) -> list[str]:
        """Values of a list section such as [InfantryTypes], de-duplicated in order."""
        seen: dict[str, None] = {}
        for value in self.section(section).values():
            seen.setdefault(value, None)
        return list(seen)
