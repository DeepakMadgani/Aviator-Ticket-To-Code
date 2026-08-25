def validate_project_member_organization_change(
    existing_organization: str, new_organization: str
) -> None:
    """
    Validates that a project member's organization is not changed after creation.

    Args:
        existing_organization: The organization currently associated with the project member.
        new_organization: The proposed new organization for the project member.

    Raises:
        ValueError: If the new_organization is different from the existing_organization.
    """
    if existing_organization != new_organization:
        # As per functional requirements, the error message must be exact.
        # The 'Org-Primary' is hardcoded in the required error message.
        error_message = (
            "Validation Error: Project member organization cannot be changed after creation. "
            "Organization is locked to Org-Primary."
        )
        raise ValueError(error_message)