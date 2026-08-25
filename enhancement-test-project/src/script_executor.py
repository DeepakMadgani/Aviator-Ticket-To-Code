import os
import sys
import io
import traceback
from typing import Dict, Any, Optional
from dataclasses import dataclass, field

@dataclass
class ScriptExecutionResult:
    """Represents the result of a script execution."""
    success: bool
    output: str = ""
    error: str = ""
    returned_globals: Dict[str, Any] = field(default_factory=dict)

def execute_python_script(script_path: str) -> ScriptExecutionResult:
    """
    Executes a Python script in an isolated environment.

    The script is executed using `exec()` with its own global and local dictionaries,
    preventing it from directly modifying the calling module's state.
    Standard output (stdout) and standard error (stderr) are captured.

    Args:
        script_path: The path to the Python script file.

    Returns:
        A ScriptExecutionResult object containing:
        - success (bool): True if the script executed without unhandled exceptions, False otherwise.
        - output (str): The captured standard output of the script.
        - error (str): The captured standard error or exception traceback.
        - returned_globals (Dict[str, Any]): The global namespace of the executed script.
                                            This can be used to retrieve variables or functions defined in the script.

    Edge Cases Handled:
    - FileNotFoundError: If the script file does not exist.
    - SyntaxError: If the script contains Python syntax errors.
    - ImportError/ModuleNotFoundError: If the script tries to import a non-existent module.
    - General Exception: Catches any other runtime exceptions.

    Limitations:
    - This function does not provide OS-level sandboxing or resource limits (e.g., CPU time, memory).
      A script with an infinite loop will block the current process.
      For true security and resource control, consider using subprocesses with timeouts
      or dedicated sandboxing libraries/containers.
    - The script runs with the same user permissions as the calling process.
    """
    captured_output = io.StringIO()
    captured_error = io.StringIO()

    original_stdout = sys.stdout
    original_stderr = sys.stderr

    # Prepare an isolated environment for the script
    # __builtins__ is necessary for basic Python functions to work
    # __file__ is set to the script_path for correct relative imports/paths within the script
    script_globals: Dict[str, Any] = {
        "__builtins__": __builtins__,
        "__name__": "__main__",
        "__file__": script_path,
        "__doc__": None,
        "__package__": None,
        "sys": sys, # Provide sys module for basic functionality if needed by script (e.g. sys.exit)
        "os": os,   # Provide os module for basic functionality if needed by script (e.g. os.path)
    }
    # For top-level script execution, locals and globals are typically the same.
    script_locals: Dict[str, Any] = script_globals

    try:
        # Redirect stdout and stderr to capture script output
        sys.stdout = captured_output
        sys.stderr = captured_error

        with open(script_path, 'r', encoding='utf-8') as f:
            script_code = f.read()

        # Execute the script in the isolated environment
        exec(script_code, script_globals, script_locals)

        return ScriptExecutionResult(
            success=True,
            output=captured_output.getvalue(),
            error=captured_error.getvalue(),
            returned_globals=script_globals
        )

    except FileNotFoundError:
        error_msg = f"Error: Script file not found at '{script_path}'"
        captured_error.write(error_msg)
        return ScriptExecutionResult(
            success=False,
            output=captured_output.getvalue(),
            error=captured_error.getvalue() + "\n" + error_msg
        )
    except SyntaxError as e:
        # Syntax errors are caught during compilation phase of exec
        error_msg = f"Error: Syntax error in script '{script_path}': {e}"
        captured_error.write(error_msg)
        return ScriptExecutionResult(
            success=False,
            output=captured_output.getvalue(),
            error=captured_error.getvalue() + "\n" + error_msg
        )
    except ImportError as e:
        error_msg = f"Error: Module import error in script '{script_path}': {e}"
        captured_error.write(error_msg)
        return ScriptExecutionResult(
            success=False,
            output=captured_output.getvalue(),
            error=captured_error.getvalue() + "\n" + error_msg
        )
    except Exception as e:
        # Catch any other runtime exceptions
        error_traceback = traceback.format_exc()
        error_msg = f"Error: Runtime exception in script '{script_path}': {e}\n{error_traceback}"
        captured_error.write(error_msg)
        return ScriptExecutionResult(
            success=False,
            output=captured_output.getvalue(),
            error=captured_error.getvalue() + "\n" + error_msg
        )
    finally:
        # Ensure stdout and stderr are restored even if an unexpected error occurs
        sys.stdout = original_stdout
        sys.stderr = original_stderr