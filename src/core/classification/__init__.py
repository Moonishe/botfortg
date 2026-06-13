"""Message classification utilities — word-pattern based intent classifier."""

from .message_classifier import MessageClassifier, classify_message, get_classifier

__all__ = ["MessageClassifier", "classify_message", "get_classifier"]
