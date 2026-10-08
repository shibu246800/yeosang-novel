import base64

import requests

from ai.providers import AIProvider


class OpenRouterProvider(AIProvider):
    """OpenRouter provider with dynamic model discovery."""

    name = "OpenRouter"

    MODELS_URL = (
        "https://openrouter.ai/api/v1/models"
    )

    CHAT_URL = (
        "https://openrouter.ai/api/v1/chat/completions"
    )

    def __init__(self, api_key: str):
        self.api_key = api_key

    # ═════════════════════════════════════
    # IMAGE ANALYSIS
    # ═════════════════════════════════════

    def analyze_image(
        self,
        image_bytes: bytes,
        mime_type: str,
        prompt: str,
    ) -> str:

        models = self._get_models(
            require_image=True
        )

        if not models:

            raise RuntimeError(
                "OpenRouter has no suitable "
                "vision models available."
            )

        encoded_image = base64.b64encode(
            image_bytes
        ).decode("utf-8")

        last_error = None

        for model in models:

            try:

                print(
                    f"🧠 Trying OpenRouter model: "
                    f"{model}"
                )

                response = requests.post(
                    self.CHAT_URL,
                    headers={
                        "Authorization": (
                            f"Bearer {self.api_key}"
                        ),
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": prompt,
                                    },
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": (
                                                f"data:{mime_type};"
                                                f"base64,"
                                                f"{encoded_image}"
                                            )
                                        },
                                    },
                                ],
                            }
                        ],
                    },
                    timeout=180,
                )

                if not response.ok:

                    print(
                        f"❌ OpenRouter {model}: "
                        f"{response.text}"
                    )

                    last_error = (
                        f"{model}: "
                        f"HTTP {response.status_code}"
                    )

                    continue

                answer = self._extract_output(
                    response.json()
                )

                if not answer:

                    last_error = (
                        f"{model}: empty response"
                    )

                    continue

                if self._looks_like_safety_only_response(
                    answer
                ):

                    last_error = (
                        f"{model}: safety-only response"
                    )

                    continue

                print(
                    f"✅ OpenRouter succeeded: "
                    f"{model}"
                )

                return answer

            except Exception as error:

                last_error = (
                    f"{model}: {error}"
                )

                print(
                    f"❌ OpenRouter error: {error}"
                )

        raise RuntimeError(
            last_error
            or "All OpenRouter vision models failed."
        )

    # ═════════════════════════════════════
    # TEXT GENERATION
    # ═════════════════════════════════════

    def generate_text(
        self,
        prompt: str,
    ) -> str:

        models = self._get_models(
            require_image=False
        )

        if not models:

            raise RuntimeError(
                "OpenRouter has no suitable "
                "free text models available."
            )

        last_error = None

        for model in models:

            try:

                print(
                    f"🧠 Trying OpenRouter text model: "
                    f"{model}"
                )

                response = requests.post(
                    self.CHAT_URL,
                    headers={
                        "Authorization": (
                            f"Bearer {self.api_key}"
                        ),
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {
                                "role": "user",
                                "content": prompt,
                            }
                        ],
                    },
                    timeout=180,
                )

                if not response.ok:

                    print(
                        f"❌ OpenRouter text {model}: "
                        f"{response.text}"
                    )

                    last_error = (
                        f"{model}: "
                        f"HTTP {response.status_code}"
                    )

                    continue

                answer = self._extract_output(
                    response.json()
                )

                if answer:

                    print(
                        f"✅ OpenRouter text succeeded: "
                        f"{model}"
                    )

                    return answer

                last_error = (
                    f"{model}: empty response"
                )

            except Exception as error:

                last_error = (
                    f"{model}: {error}"
                )

        raise RuntimeError(
            last_error
            or "All OpenRouter text models failed."
        )

    # ═════════════════════════════════════
    # MODEL DISCOVERY
    # ═════════════════════════════════════

    def _get_models(
        self,
        require_image=False,
    ):

        response = requests.get(
            self.MODELS_URL,
            timeout=30,
        )

        response.raise_for_status()

        data = response.json()

        candidates = []

        for model in data.get(
            "data",
            []
        ):

            if not isinstance(model, dict):
                continue

            model_id = model.get("id")

            if not model_id:
                continue

            if model_id == "openrouter/free":
                continue

            architecture = model.get(
                "architecture",
                {}
            )

            modalities = architecture.get(
                "input_modalities",
                []
            )

            if require_image:

                if "image" not in modalities:
                    continue

            pricing = model.get(
                "pricing",
                {}
            )

            if not self._is_free_price(
                pricing.get("prompt")
            ):
                continue

            if not self._is_free_price(
                pricing.get("completion")
            ):
                continue

            candidates.append(
                model
            )

        candidates.sort(
            key=lambda item: item.get(
                "context_length",
                0
            ),
            reverse=True,
        )

        return [
            model["id"]
            for model in candidates[:8]
        ]

    @staticmethod
    def _is_free_price(value):

        try:
            return float(value or 0) == 0
        except (
            TypeError,
            ValueError,
        ):
            return False

    @staticmethod
    def _extract_output(data):

        choices = data.get(
            "choices",
            []
        )

        if not choices:
            return None

        message = choices[0].get(
            "message",
            {}
        )

        content = message.get(
            "content"
        )

        if isinstance(content, str):

            content = content.strip()

            if content:
                return content

        if isinstance(content, list):

            parts = []

            for item in content:

                if not isinstance(item, dict):
                    continue

                text = item.get("text")

                if text:
                    parts.append(text)

            answer = "\n".join(
                parts
            ).strip()

            if answer:
                return answer

        return None

    @staticmethod
    def _looks_like_safety_only_response(
        answer
    ):

        return answer.strip().lower() in {
            "safe",
            "unsafe",
            "user safety: safe",
            "user safety: unsafe",
            }
