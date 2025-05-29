def filter_username(text: str):
    return text.lstrip("@").replace("\U00010000", '').replace("|","").replace("/","")
