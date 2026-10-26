"""Parse an ordered NodePort pool without assuming the cluster's port range."""

import re

MAX_POOL_PORTS = 4096


def parse_nodeport_pool(value: str) -> list[int]:
    if not value.strip():
        return []
    ports: dict[int, None] = {}
    for entry in value.split(","):
        match = re.fullmatch(r"\s*([0-9]+)\s*(?:-\s*([0-9]+)\s*)?", entry)
        if not match:
            raise ValueError("use comma-separated ports or ranges, e.g. 31080-31089,31100")
        first = int(match[1])
        last = int(match[2] or match[1])
        if not 1 <= first <= last <= 65535:
            raise ValueError("NodePort ranges must be ascending and between 1 and 65535")
        if last - first + 1 > MAX_POOL_PORTS:
            raise ValueError(f"NodePort pool must contain at most {MAX_POOL_PORTS} ports")
        ports.update(dict.fromkeys(range(first, last + 1)))
        if len(ports) > MAX_POOL_PORTS:
            raise ValueError(f"NodePort pool must contain at most {MAX_POOL_PORTS} ports")
    return list(ports)
