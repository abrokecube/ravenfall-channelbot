from .modals import Flag
import re

DELIMETERS = ('=', ':')
RE_FLAG = re.compile(r'[-a-zA-Z]{2}[a-zA-Z]+[:=]+.+|-[a-zA-Z]\b|--[a-zA-Z_]+\b')

class CommandArgs:
    def __init__(self, text: str):
        self.text = text
        
        self.args: list[str | Flag] = []  # args are in order of appearance
        self.flags: list[Flag] = []  # flags are in order of appearance
        self.grouped_args: list[str] = []  # consecutive non-flag args joined by space
        self._parse()

    def _parse(self):
        if not self.text.strip():
            return
        
        in_quotes = None  # None if not in quotes, otherwise the quote char (' or ")
        current = []
        args: list[str] = []
        i = 0
        n = len(self.text)
        
        while i < n:
            char = self.text[i]
            
            # Handle quotes
            if char in ('"', "'"):
                if i > 0 and self.text[i-1] == '\\':
                    # Escaped quote, add to current and remove the backslash
                    current[-1] = char
                elif in_quotes is None:
                    # Start of quoted string
                    current.append('"')
                    in_quotes = char
                elif char == in_quotes:
                    # End of quoted string
                    current.append('"')
                    in_quotes = None
                else:
                    # Nested quotes of different type, add to current
                    current.append(char)
            elif char.isspace() and in_quotes is None:
                if current:
                    args.append(''.join(current))
                    current = []
            else:
                current.append(char)
                
            i += 1
                
        if current:
            args.append(''.join(current))
        
        for arg in args:
            delimiter_char = None
            has_delimiter = False
            for delimiter in DELIMETERS:
                if delimiter in arg:
                    has_delimiter = True
                    delimiter_char = delimiter
                    break
            is_quoted = arg[0] == '"' and arg[-1] == '"'
            if RE_FLAG.match(arg):
                flag_name: str = arg.lstrip('-')
                flag_value: str | None = True
                if has_delimiter:
                    if delimiter_char in flag_name:
                        flag_name, flag_value = flag_name.split(delimiter_char, 1)
                if isinstance(flag_value, str) and flag_value[0] == '"' and flag_value[-1] == '"':
                    flag_value = flag_value[1:-1]
                flag = Flag(flag_name, flag_value)
                self.flags.append(flag)
                self.args.append(flag)
            else:
                if is_quoted:
                    arg = arg[1:-1]
                self.args.append(arg)

        # Build grouped_args by joining consecutive non-flag args with spaces,
        # using flags as separators (flags are not included in grouped_args)
        grouped: list[str] = []
        current_group: list[str] = []
        for item in self.args:
            if isinstance(item, Flag):
                if current_group:
                    grouped.append(' '.join(current_group))
                    current_group = []
            else:
                current_group.append(item)
        if current_group:
            grouped.append(' '.join(current_group))
        self.grouped_args = grouped

    def get_flag(self, name: str | list[str], case_sensitive: bool = False, default: str | None = None) -> Flag | None:
        names = name if isinstance(name, list) else [name]
        for flag in self.flags:
            if case_sensitive and flag.name in names:
                return flag
            elif not case_sensitive and flag.name.lower() in [n.lower() for n in names]:
                return flag
        return Flag(name, default)
