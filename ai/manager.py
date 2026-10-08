import os

from ai.gemini import GeminiProvider
from ai.openrouter import OpenRouterProvider


class AIManager:
    """
    Controls Yeosang Novel's AI providers.

    Providers are tried in priority order.
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

        if not self.providers:

            raise RuntimeError(
                "No AI providers are configured. "
                "Add GEMINI_API_KEY or "
                "OPENROUTER_API_KEY to Render."
            )

        errors = []

        for provider in self.providers:

            print(
                f"🤖 AI Manager → "
                f"{provider.name}"
            )

            try:

                result = provider.analyze_image(
                    image_bytes=image_bytes,
                    mime_type=mime_type,
                    prompt=prompt,
                )

                print(
                    f"✅ AI Manager succeeded with "
                    f"{provider.name}"
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
