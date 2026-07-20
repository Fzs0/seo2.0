class KnowledgeError(Exception):
    """Base class for errors deliberately exposed by the knowledge module."""


class InvalidInputError(KnowledgeError):
    pass


class UrlFetchError(InvalidInputError):
    """A user-facing failure while safely fetching a public web page."""

    pass


class NotFoundError(KnowledgeError):
    pass


class ConflictError(KnowledgeError):
    pass


class DatabaseUnavailableError(KnowledgeError):
    pass
