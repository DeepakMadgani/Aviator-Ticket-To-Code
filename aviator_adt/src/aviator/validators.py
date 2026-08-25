"""Custom validators for Aviator settings."""

from pydantic import AnyUrl
from pydantic.networks import UrlConstraints


class PubSubUrl(AnyUrl):
    """A type that will accept Google Pub/Sub URLs.

    Supported format:
        - gcpubsub://projects/project-id
        - gcpubsub://projects/project-id/topic_name

    Examples:
        - gcpubsub://projects/my-project
        - gcpubsub://projects/aviator-dev/embeddings-queue

    """

    _constraints = UrlConstraints(
        allowed_schemes=["gcpubsub"],
        host_required=True,
    )

    def __init__(self, url: str) -> None:
        """Initialize and validate the URL format."""
        super().__init__(url)
        # Validate the format after the URL is parsed
        if self.host != "projects":
            from pydantic_core import PydanticCustomError

            err = "value_error"
            raise PydanticCustomError(err, "PubSub URL must start with 'gcpubsub://projects/'", {})
        if not self.path or not self.path.lstrip("/"):
            from pydantic_core import PydanticCustomError

            err = "value_error"
            raise PydanticCustomError(err, "PubSub URL must include a project ID after 'projects/'", {})

    @property
    def project_id(self) -> str:
        """The Google Cloud project ID."""
        if self.host == "projects" and self.path:
            path_parts = self.path.lstrip("/").split("/")
            if path_parts:
                return path_parts[0]

        msg = "Invalid PubSub URL format"
        raise ValueError(msg)

    @property
    def topic_name(self) -> str:
        """The Pub/Sub topic name."""
        if self.host == "projects" and self.path:
            path_parts = self.path.lstrip("/").split("/")
            if len(path_parts) > 1:
                return path_parts[1]
            return ""
        return ""
