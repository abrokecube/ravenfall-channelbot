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

def split_by_utf16_bytes(text: str, max_bytes: int) -> list[str]:
    words = text.split(' ')
    parts = []
    current = ''
    current_bytes = 0
    max_bytes *= 2

    for word in words:
        if current:
            sep = ' '
            sep_bytes = 2  # UTF-16 bytes for space
        else:
            sep = ''
            sep_bytes = 0

        word_bytes = len(word.encode('utf-16-le'))
        total_bytes = current_bytes + sep_bytes + word_bytes

        if total_bytes > max_bytes:
            if current:
                parts.append(current)
                current = word
                current_bytes = word_bytes
            else:
                # Single word too long; force split it (optional)
                split_word = split_long_word_utf16(word, max_bytes)
                parts.extend(split_word[:-1])
                current = split_word[-1]
                current_bytes = len(current.encode('utf-16-le'))
        else:
            current += sep + word
            current_bytes = total_bytes

    if current:
        parts.append(current)

    return parts


def split_long_word_utf16(word: str, max_bytes: int) -> list[str]:
    parts = []
    current = ''
    current_bytes = 0
    for char in word:
        char_bytes = len(char.encode('utf-16-le'))
        if current_bytes + char_bytes > max_bytes:
            if current:
                parts.append(current)
            current = char
            current_bytes = char_bytes
        else:
            current += char
            current_bytes += char_bytes
    if current:
        parts.append(current)
    return parts

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