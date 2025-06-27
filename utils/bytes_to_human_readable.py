def bytes_to_human_readable(size_bytes):
    negative = ''
    if size_bytes < 0:
        size_bytes = -size_bytes
        negative = '-'
    if size_bytes == 0:
        return "0 B"
    
    units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB']
    power = 1024
    unit_index = 0

    while size_bytes >= power and unit_index < len(units) - 1:
        size_bytes /= power
        unit_index += 1

    return f"{negative}{size_bytes:.2f} {units[unit_index]}"
