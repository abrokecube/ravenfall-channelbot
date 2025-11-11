import re

def fill_whitespace(text: str, pattern: str = ". "):
    """
    Replace whitespace runs with a repeated pattern, keeping a single real space
    at each edge of the run. The total length of the run is preserved.

    Example:
        "a          b" -> "a . . . .  b"
    """
    def repl(m):
        run = m.group(0)
        run_len = len(run)
        if run_len <= 2:
            # Too short to fit pattern inside — leave as-is
            return run

        # Keep 1 space at each end
        inner_len = run_len - 2
        repeated = (pattern * ((inner_len // len(pattern)) + 1))[:inner_len]

        return " " + repeated + " "

    return re.sub(r' +', repl, text)


# Example
# s = "a          b"  # 10 spaces
s = """
    Abraxas Ore                    129650
    Abraxas Spirit                      0
    Adamantite Nugget                   0
    Adamantite Ore                 379944
    Ancient Heart                       0
    Ancient Ore                     17458
    Apple                           78875
    Atlarus Light                       0

"""
print(fill_whitespace(s, pattern=". "))
