from enum import Enum
from datetime import timedelta

class TimeSize(Enum):
    SMALL = 0
    SMALL_SPACES = 1
    MEDIUM = 2
    MEDIUM_SPACES = 3
    LONG = 4

_time_str = {
    'day': ('d', 'd', 'day', ' day', ' day'),
    'days': ('d', 'd', 'days', ' days', ' days'),
    'hour': ('h', 'h', 'hr', ' hr', ' hour'),
    'hours': ('h', 'h', 'hrs', ' hrs', ' hours'),
    'minute': ('m', 'm', 'min', ' min', ' minute'),
    'minutes': ('m', 'm', 'mins', ' mins', ' minutes'),
    'second': ('s', 's', 'sec', ' sec', ' second'),
    'seconds': ('s', 's', 'secs', ' secs', ' seconds'),
}

def format_timedelta(td: timedelta, size=TimeSize.SMALL_SPACES, max_terms=99) -> str:  
    return format_seconds(int(td.total_seconds()), size, max_terms)


def format_seconds(seconds: int, size=TimeSize.SMALL, max_terms=99, include_zero=True):
    seconds = int(seconds)
    total_seconds = seconds
    negative = False
    if seconds < 0:
        seconds = -seconds
        negative = True
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    
    parts = []
    if days:
        word = _time_str['day'][size.value] if days == 1 else _time_str['days'][size.value]
        parts.append(f"{days}{word}")
    if hours or (include_zero and days) :
        word = _time_str['hour'][size.value] if hours == 1 else _time_str['hours'][size.value]
        parts.append(f"{hours}{word}")
    if minutes or (include_zero and any((hours, days))) :
        word = _time_str['minute'][size.value] if minutes == 1 else _time_str['minutes'][size.value]
        parts.append(f"{minutes}{word}")
    if seconds or (include_zero and any((minutes, hours, days))) or not parts:
        word = _time_str['second'][size.value] if seconds == 1 else _time_str['seconds'][size.value]
        parts.append(f"{seconds}{word}")
    if size == TimeSize.LONG and len(parts) > 1:
        # parts[-1] = "and " + parts[-1]
        # bye oxford comma
        last = parts.pop()
        parts[-1] += f" and {last}" 
    parts = parts[:max_terms]
    
    if negative:
        parts[0] = f"-{parts[0]}"
    
    if size == TimeSize.LONG:
        return ", ".join(parts).strip()
    elif size in [TimeSize.MEDIUM, TimeSize.MEDIUM_SPACES, TimeSize.SMALL_SPACES]:
        return " ".join(parts).strip()
    else:
        return "".join(parts).strip()

def seconds_to_dhms(seconds):
    seconds = int(seconds)
    days, remainder = divmod(seconds, 86400)        # 86400 seconds in a day
    hours, remainder = divmod(remainder, 3600)      # 3600 seconds in an hour
    minutes, seconds = divmod(remainder, 60)
    return f"{days}:{hours:02}:{minutes:02}:{seconds:02}"
