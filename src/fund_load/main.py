from __future__ import annotations

import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    print("heol wrlod")

    all_args = argv

    path = all_args[0]

    print(f"input here {path}")

    # TODO: позже Codex сюда добавит реальный CLI-парсинг
    # сейчас просто “пустой лист”
    return 0
