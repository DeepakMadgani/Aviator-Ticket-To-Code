"""Define exceptions."""


class EmbeddingError(Exception):
    """Exception raised for errors during the embedding Process.

    Attributes:
        message - explanation of the error
        code    - optional error code (int)

    Error Codes:
        | Code | Description                                      |
        |------|--------------------------------------------------|
        | 000  | undefined Error                                  |
        | 100  | Error while storing embedding in vector store    |
        | 101  | Delete operation requires metadata filter        |
        | 102  | Delete operation requires documentID in metadata |
        | 103  | Error while deleting documents from vector store |
        | 104  | No content chunks to store in vector store       |

    """

    def __init__(self, code: int, message: str) -> None:  # noqa: D107
        super().__init__(message)
        self.message = message
        self.code = code

    def __str__(self) -> str:  # noqa: D105
        if self.code is not None:
            return f"[Error {self.code}] {self.message}"
        return self.message


class WorkspaceSummaryError(Exception):
    """Exception raised for errors during the workspace or document summary generation process.

    Attributes:
        message - explanation of the error
        code    - optional error code (int)

    Error Codes:
        | Code | Description                                      |
        |------|--------------------------------------------------|
        | 201  | Summarization request missing content            |
        | 202  | LLM configuration error (invalid model/provider) |        |
        | 203  | Prompt template formatting error                 |
        | 205  | LLM returned empty summary                       |
        | 206  | Database error while storing summary             |

    """

    def __init__(self, code: int, message: str) -> None:  # noqa: D107
        super().__init__(message)
        self.message = message
        self.code = code

    def __str__(self) -> str:  # noqa: D105
        if self.code is not None:
            return f"[Summarization Error {self.code}] {self.message}"
        return self.message


class EmbeddingRetryableError(EmbeddingError):
    """Exception raised for retryable errors during the embedding Process.

    Inherits from EmbeddingError.
    """


class WorkspaceSummaryRetryableError(WorkspaceSummaryError):
    """Exception raised for retryable workspace summary failures.

    Inherits from WorkspaceSummaryError.
    """


class PromptError(Exception):
    """Base exception for prompt-related errors."""


class PromptNotFoundError(PromptError):
    """Error raised when a prompt file is not found."""

    def __init__(self, prompt_name: str, model: str | None = None) -> None:
        """Initialize PromptNotFound exception.

        Args:
            prompt_name: The name of the prompt that was not found.
            model: Optional model name for which the prompt was not found.

        """
        self.prompt_name = prompt_name
        self.model = model
        super().__init__(self._message())

    def _message(self) -> str:
        """Generate error message based on prompt name and model."""
        if self.model:
            return f"Prompt '{self.prompt_name}' not found for model '{self.model}'"
        return f"Prompt '{self.prompt_name}' not found"


class EmbeddingDimensionMismatchError(Exception):
    """Exception raised for embedding dimension mismatch errors."""


class TenantError(Exception):
    """Base exception for tenant-related errors."""

    def __init__(self, message: str) -> None:
        """Initialize TenantError."""
        super().__init__(message)
        self.message = message


class TenantNotFoundError(TenantError):
    """Exception raised when a tenant schema does not exist."""


class TenantAlreadyExistsError(TenantError):
    """Exception raised when trying to create a tenant that already exists."""


class MCPToolAlreadyRegisteredError(Exception):
    """Exception raised when trying to register tools that are already configured."""


class A2ATaskError(Exception):
    """Base exception for A2A task-related errors."""

    def __init__(self, message: str) -> None:
        """Initialise with a message."""
        super().__init__(message)
        self.message = message


class TaskNotFoundError(A2ATaskError):
    """Exception raised when an A2A task ID cannot be found."""


class TaskNotCancelableError(A2ATaskError):
    """Exception raised when a task is in a terminal state and cannot be cancelled."""
