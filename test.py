def truncate_by_bytes(s: str, max_bytes: int, encoding='utf-8') -> str:
    encoded = s.encode(encoding)
    if len(encoded) <= max_bytes:
        return s

    # Truncate encoded bytes safely
    truncated = encoded[:max_bytes]
    # Try decoding, reducing size until it works
    while True:
        try:
            return truncated.decode(encoding)
        except UnicodeDecodeError:
            truncated = truncated[:-1]
print(truncate_by_bytes("Hell🚨🚨🚨 world!", 5))