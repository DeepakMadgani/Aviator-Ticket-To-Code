"""Shared chat history validation helpers for chat endpoints."""


class ChatHistoryValidationError(ValueError):
    """Base exception for invalid chat history payloads."""


class ChatHistoryStartEndUserError(ChatHistoryValidationError):
    """Raised when chat history does not start and end with a user message."""

    def __init__(self) -> None:
        """Create the start/end role validation error."""
        super().__init__("Chat history must start and end with a user message.")


class ChatHistoryConsecutiveRoleError(ChatHistoryValidationError):
    """Raised when chat history has consecutive messages from the same role."""

    def __init__(self) -> None:
        """Create the consecutive-role validation error."""
        super().__init__("Chat history must not contain consecutive messages from the same role.")


class ChatHistoryValidator:
    """Validate and normalize chat history role ordering rules."""

    _USER_ROLES = {"human", "user"}
    _AI_ROLES = {"ai", "assistant"}

    @classmethod
    def _role_group(cls, author: str) -> str:
        if author in cls._USER_ROLES:
            return "user"
        if author in cls._AI_ROLES:
            return "ai"
        return author

    @classmethod
    def validate(cls, messages: list[tuple[str, str]]) -> None:
        """Validate chat history message ordering.

        Rules:
        - Messages must start and end with a user message.
        - No two consecutive messages from the same role.

        Raises:
            ChatHistoryValidationError: If the history format is invalid.

        """
        if not messages:
            return

        first_role = cls._role_group(messages[0][0])
        last_role = cls._role_group(messages[-1][0])

        if first_role != "user" or last_role != "user":
            raise ChatHistoryStartEndUserError

        for i in range(1, len(messages)):
            if cls._role_group(messages[i][0]) == cls._role_group(messages[i - 1][0]):
                raise ChatHistoryConsecutiveRoleError
