def strjoin(connecting_char: str, *strings: str, before_end: str | None=None, include_conn_char_before_end=False):
    str_list = [str(x) for x in strings if x]
    if len(str_list) > 1 and before_end is not None:
        if include_conn_char_before_end:
            str_list[-1] = f"{before_end}{str_list[-1]}"
        else:
            a = str_list.pop()
            str_list[-1] += f"{before_end}{a}"
    
    return connecting_char.join(str_list)

def strjoin_list(connecting_char: str, *strings: str, before_end: str | None=None, include_conn_char_before_end=False):
    str_list = [str(x) for x in strings if x]
    if len(str_list) > 1 and before_end is not None:
        if include_conn_char_before_end:
            str_list[-1] = f"{before_end}{str_list[-1]}"
        else:
            a = str_list.pop()
            str_list[-1] += f"{before_end}{a}"
    out_list = []
    for string in str_list:
        out_list.append(string)
        out_list.append(connecting_char)
    out_list.pop()
    return out_list

def strenclose(open_char: str, close_char: str, connecting_char: str, *strings: str):
    out = [open_char + str(x) + close_char for x in strings if x]
    if len(out) == 0:
        return None
    return connecting_char.join(out)

def strjoin_len(connecting_char: str, max_chars: int, *strings: str):
    result, current = [], ""

    for s in filter(None, strings):
        if current:
            candidate = current + connecting_char + s
        else:
            candidate = s
        
        if len(candidate) > max_chars:
            result.append(current)
            current = s
        else:
            current = candidate
    
    if current:
        result.append(current)
    
    return result

def strextend(base, max_chars: int, *strings: str):
    if isinstance(base, str):
        result, current = [], base
    elif isinstance(base, list):
        result, current = base[:-1], base[-1] if base else ""
    else:
        raise TypeError("Base must be a string or a list of strings")
    
    for s in filter(None, strings):
        if current:
            candidate = current + s
        else:
            candidate = s
        
        if len(candidate) > max_chars:
            result.append(current)
            current = s
        else:
            current = candidate
    
    if current:
        result.append(current)
    
    return result
def strprefix(prefix: str, string: str):
    if string:
        return f"{prefix}{string}"
    else:
        return ''
def rm_words(string: str, num: int):
    split = string.split(' ')
    if num > 0:
        return " ".join(split[num:])
    elif num < 0:
        return " ".join(split[:num])
    else:
        return string
        
def split_by_bytes(s: str, max_bytes: int, encoding: str = 'utf-16-le') -> list[str]:
    """
    Split a string into multiple strings, each not exceeding max_bytes in size.
    For UTF-16, max_bytes should be even to avoid breaking surrogate pairs.
    
    Args:
        s: The input string to split
        max_bytes: Maximum number of bytes per chunk (should be even for UTF-16)
        encoding: Character encoding to use (default: 'utf-16-le')
    """
    if not s:
        return []
    
    # Ensure max_bytes is even for UTF-16
    if '16' in encoding:
        max_bytes = max_bytes // 2 * 2  # Round down to nearest even number
    
    encoded = s.encode(encoding)
    total_bytes = len(encoded)
    
    if total_bytes <= max_bytes:
        return [s]
    
    result = []
    current_pos = 0
    
    while current_pos < total_bytes:
        # Calculate end position for this chunk
        end_pos = min(current_pos + max_bytes, total_bytes)
        chunk = encoded[current_pos:end_pos]
        
        # For UTF-16, ensure we don't cut in the middle of a surrogate pair
        if '16' in encoding and len(chunk) % 2 != 0 and end_pos < total_bytes:
            chunk = chunk[:-1]  # Remove last byte to keep it even
            end_pos -= 1
        
        try:
            chunk_str = chunk.decode(encoding)
            result.append(chunk_str)
            current_pos = end_pos
        except UnicodeDecodeError:
            # If we can't decode, try reducing the chunk size
            if len(chunk) > 0:
                chunk = chunk[:-2]  # Remove last 2 bytes (one UTF-16 character)
                try:
                    chunk_str = chunk.decode(encoding)
                    result.append(chunk_str)
                    current_pos = len(b''.join(s.encode(encoding) for s in result))
                except UnicodeDecodeError:
                    # If still can't decode, skip this character
                    current_pos += 2
    
    return [chunk for chunk in result if chunk.strip()]

def truncate_by_bytes(s: str, max_bytes: int, start_byte: int = 0, encoding: str = 'utf-16-le') -> str:
    """
    Truncate a string to a maximum number of bytes in UTF-16 encoding.
    
    Args:
        s: The input string to truncate
        max_bytes: Maximum number of bytes to keep (will be rounded down to nearest even number)
        start_byte: Starting byte position (default: 0)
        encoding: Character encoding to use (default: 'utf-16-le')
    """
    # Ensure max_bytes is even for UTF-16
    if '16' in encoding:
        max_bytes = max_bytes // 2 * 2  # Round down to nearest even number
    
    encoded = s.encode(encoding)
    total_bytes = len(encoded)
    
    # Adjust start_byte if it's negative (counting from the end)
    if start_byte < 0:
        start_byte = max(0, total_bytes + start_byte)
    
    # Ensure start_byte is even for UTF-16
    if '16' in encoding:
        start_byte = start_byte // 2 * 2
    
    # If start_byte is beyond the string length, return empty string
    if start_byte >= total_bytes:
        return ''
        
    # Get the substring starting from start_byte
    encoded = encoded[start_byte:]
    
    if len(encoded) <= max_bytes:
        return encoded.decode(encoding)
    
    # Truncate to max_bytes, ensuring we don't cut in the middle of a surrogate pair
    truncated = encoded[:max_bytes]
    if '16' in encoding and len(truncated) % 2 != 0:
        truncated = truncated[:-1]  # Remove last byte to keep it even
    
    return truncated.decode(encoding, errors='ignore')