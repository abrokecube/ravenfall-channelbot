def filter_username(text: str):
    return text.lstrip("@").replace("|","").replace("/","")
