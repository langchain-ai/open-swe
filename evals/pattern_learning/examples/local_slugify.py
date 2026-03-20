def slugify(value: str) -> str:
    lowered = value.strip().lower().replace("_", "-")
    return "-".join(part for part in lowered.split("-") if part)
