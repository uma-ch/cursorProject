from __future__ import annotations

import os
import subprocess


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


RUN_COMMAND_SCHEMA = {
    "name": "run_command",
    "description": "Execute a shell command and return its output. Use for running scripts, installing packages, compiling code, running tests, or any terminal operation.",
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute.",
            },
            "working_directory": {
                "type": "string",
                "description": "Working directory for the command. Defaults to current directory.",
            },
            "timeout": {
                "type": "integer",
                "description": "Maximum seconds to wait for the command to complete. Defaults to 120.",
            },
        },
        "required": ["command"],
    },
}


def run_command(command: str, working_directory: str = ".", timeout: int = 120) -> str:
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=working_directory,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += result.stderr
        output += f"\n[exit code: {result.returncode}]"
        return output.strip()
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"


ALL_TOOLS = [
    (READ_FILE_SCHEMA, read_file),
    (LIST_DIRECTORY_SCHEMA, list_directory),
    (RUN_COMMAND_SCHEMA, run_command),
]
