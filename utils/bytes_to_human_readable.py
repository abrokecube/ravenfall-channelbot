def bytes_to_human_readable(size_bytes: int) -> str:
    negative = ''
    if size_bytes < 0:
        size_bytes = -size_bytes
        negative = '-'
    if size_bytes == 0:
        return "0 B"
    
    units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB']
    power = 1024
    unit_index = 0

    bytes_value: float = size_bytes
    while bytes_value >= power and unit_index < len(units) - 1:
        bytes_value /= power
        unit_index += 1

    return f"{negative}{bytes_value:.2f} {units[unit_index]}"
