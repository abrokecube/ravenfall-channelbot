def filter_username(text: str):
    return text.lstrip("@").replace("\U000e0000", '').replace("|","").replace("/","")
