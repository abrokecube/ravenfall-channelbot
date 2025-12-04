import re
from typing import Dict, List, Optional, Any, Tuple

def parse_google_docstring(docstring: str) -> Dict[str, Any]:
    """Parses a Google-style docstring.

    Args:
        docstring: The docstring to parse.

    Returns:
        A dictionary containing:
        - summary: The first line/paragraph.
        - description: The detailed description.
        - args: A dictionary of argument names to (type, description) tuples.
        - examples: A list of example strings.
    """
    if not docstring:
        return {
            "summary": "",
            "description": "",
            "args": {},
            "examples": []
        }

    lines = docstring.strip().split('\n')
    summary = lines[0].strip()
    description = []
    args = {}
    examples = []

    current_section = "description"
    # Skip summary
    i = 1
    
    while i < len(lines):
        line = lines[i].strip()
        
        if not line:
            if current_section == "description":
                description.append("")
            i += 1
            continue

        if line.lower() == "args:":
            current_section = "args"
            i += 1
            continue
        elif line.lower() == "examples:":
            current_section = "examples"
            i += 1
            continue
        elif line.lower() in ("returns:", "raises:", "yields:"):
            current_section = "other"
            i += 1
            continue

        if current_section == "description":
            description.append(line)
        elif current_section == "args":
            # Parse arg line: "name (type): description" or "name: description"
            match = re.match(r'^(\w+)(?:\s*\(([^)]+)\))?:\s*(.+)$', line)
            if match:
                arg_name = match.group(1)
                arg_type = match.group(2)
                arg_desc = match.group(3)
                args[arg_name] = {"type": arg_type, "description": arg_desc}
            else:
                # Continuation of previous arg description
                # This is a simple parser, might need more robustness for multi-line args
                pass
        elif current_section == "examples":
            examples.append(line)
            
        i += 1

    return {
        "summary": summary,
        "description": "\n".join(description).strip(),
        "args": args,
        "examples": examples
    }
