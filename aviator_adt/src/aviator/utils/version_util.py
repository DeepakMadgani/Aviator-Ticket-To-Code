"""Version utility functions for the Aviator application."""

import os
from importlib.metadata import PackageNotFoundError, version


def get_aviator_version() -> str:
    """Get the current version of the Aviator application.

    Returns:
        str: The version string of the aviator package

    """
    try:
        commit_short_sha = os.getenv("COMMIT_SHORT_SHA")
        release_version = os.getenv("RELEASE_VERSION")

        if commit_short_sha and release_version:
            return f"{release_version}+{commit_short_sha}"

        if commit_short_sha or release_version:
            return commit_short_sha or release_version or "unknown"

        return version("aviator")
    except PackageNotFoundError:
        return "unknown"
