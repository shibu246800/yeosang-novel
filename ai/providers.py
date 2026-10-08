from abc import ABC, abstractmethod


class AIProvider(ABC):
    """Base interface for every Yeosang Novel AI provider."""

    name = "unknown"

    @abstractmethod
    def analyze_image(
        self,
        image_bytes: bytes,
        mime_type: str,
        prompt: str,
    ) -> str:
        """Analyze an image and return text."""
        raise NotImplementedError

    @abstractmethod
    def generate_text(
        self,
        prompt: str,
    ) -> str:
        """Generate text from a prompt."""
        raise NotImplementedError
