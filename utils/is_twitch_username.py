import re
tw_username_re = re.compile(r"^@?[a-zA-Z0-9][\w]{2,24}$")
tw_username_f_re = re.compile(r"^@?[a-zA-Z0-9/|][\w/|]{2,24}$")
def is_twitch_username(text: str, pre_filter=False):
    if pre_filter:
        return bool(tw_username_f_re.match(text))
    else:
        return bool(tw_username_re.match(text))

