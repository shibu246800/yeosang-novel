import os

from ai.gemini import GeminiProvider
from ai.openrouter import OpenRouterProvider


class AIManager:
    """
    Controls Yeosang Novel's AI providers.

    Gemini is tried first.
    OpenRouter is the fallback.
    """

    def __init__(self):

        self.providers = []

        gemini_key = os.environ.get(
            "GEMINI_API_KEY"
        )

        if gemini_key:

            self.providers.append(
                GeminiProvider(
                    api_key=gemini_key
                )
            )

        openrouter_key = os.environ.get(
            "OPENROUTER_API_KEY"
        )

        if openrouter_key:

            self.providers.append(
                OpenRouterProvider(
                    api_key=openrouter_key
                )
            )

    def analyze_image(
        self,
        image_bytes: bytes,
        mime_type: str,
        prompt: str,
    ):

        return self._run(
            "analyze_image",
            image_bytes=image_bytes,
            mime_type=mime_type,
            prompt=prompt,
        )

    def generate_text(
        self,
        prompt: str,
    ):

        return self._run(
            "generate_text",
            prompt=prompt,
        )

    def _run(
        self,
        method_name,
        **kwargs,
    ):

        if not self.providers:

            raise RuntimeError(
                "No AI providers are configured."
            )

        errors = []

        for provider in self.providers:

            print(
                f"🤖 AI Manager → "
                f"{provider.name}"
            )

            try:

                method = getattr(
                    provider,
                    method_name,
                )

                result = method(
                    **kwargs
                )

                print(
                    f"✅ AI Manager succeeded "
                    f"with {provider.name}"
                )

                return result, provider.name

            except Exception as error:

                print(
                    f"❌ {provider.name} failed: "
                    f"{error}"
                )

                errors.append(
                    f"{provider.name}: {error}"
                )

                print(
                    "🔁 Trying the next provider..."
                )

        raise RuntimeError(
            "All AI providers failed.\n\n"
            + "\n".join(errors)
        )
