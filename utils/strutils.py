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
