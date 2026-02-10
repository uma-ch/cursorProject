from __future__ import annotations

import os


READ_FILE_SCHEMA = {
    "name": "read_file",
    "description": "Read the contents of a file at the given path.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute or relative path to the file to read.",
            }
        },
        "required": ["path"],
    },
}


LIST_DIRECTORY_SCHEMA = {
    "name": "list_directory",
    "description": "List files and directories at the given path.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute or relative path to the directory to list. Defaults to the current directory.",
            }
        },
        "required": [],
    },
}


def read_file(path: str) -> str:
    with open(path, "r") as f:
        return f.read()


def list_directory(path: str = ".") -> str:
    entries = os.listdir(path)
    return "\n".join(sorted(entries))


ALL_TOOLS = [
    (READ_FILE_SCHEMA, read_file),
    (LIST_DIRECTORY_SCHEMA, list_directory),
]
