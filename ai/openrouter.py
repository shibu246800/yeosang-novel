import base64

import requests

from ai.providers import AIProvider


class OpenRouterProvider(AIProvider):
    """OpenRouter provider with dynamic free vision-model discovery."""

    name = "OpenRouter"

    MODELS_URL = (
        "https://openrouter.ai/api/v1/models"
    )

    CHAT_URL = (
        "https://openrouter.ai/api/v1/chat/completions"
    )

    def __init__(self, api_key: str):
        self.api_key = api_key

    def analyze_image(
        self,
        image_bytes: bytes,
        mime_type: str,
        prompt: str,
    ) -> str:

        models = self._get_free_vision_models()

        if not models:

            raise RuntimeError(
                "OpenRouter has no currently available "
                "free vision models."
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

                print(
                    f"📡 OpenRouter status: "
                    f"{response.status_code}"
                )

                if not response.ok:

                    print(
                        f"❌ OpenRouter {model}: "
                        f"{response.text}"
                    )

                    last_error = (
                        f"{model}: "
                        f"HTTP {response.status_code} "
                        f"{response.text}"
                    )

                    continue

                data = response.json()

                answer = self._extract_output(
                    data
                )

                if not answer:

                    last_error = (
                        f"{model}: empty response"
                    )

                    print(
                        f"⚠️ {model} returned "
                        "no usable text."
                    )

                    continue

                # Some routing/model combinations can
                # return an unhelpful safety-only response.
                if self._looks_like_safety_only_response(
                    answer
                ):

                    last_error = (
                        f"{model}: unusable safety-only "
                        "response"
                    )

                    print(
                        f"⚠️ {model} returned a "
                        "safety-only response."
                    )

                    continue

                print(
                    f"✅ OpenRouter succeeded: "
                    f"{model}"
                )

                return answer

            except requests.exceptions.Timeout:

                last_error = (
                    f"{model}: request timed out"
                )

                print(
                    f"⏱️ {model} timed out."
                )

            except requests.exceptions.RequestException as error:

                last_error = (
                    f"{model}: {error}"
                )

                print(
                    f"❌ OpenRouter request error: "
                    f"{error}"
                )

            except ValueError as error:

                last_error = (
                    f"{model}: invalid JSON"
                )

                print(
                    f"❌ Invalid OpenRouter JSON: "
                    f"{error}"
                )

        raise RuntimeError(
            last_error
            or "All OpenRouter vision models failed."
        )

    def _get_free_vision_models(self):

        response = requests.get(
            self.MODELS_URL,
            timeout=30,
        )

        response.raise_for_status()

        data = response.json()

        models = data.get(
            "data",
            []
        )

        candidates = []

        for model in models:

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

            if "image" not in modalities:
                continue

            pricing = model.get(
                "pricing",
                {}
            )

            prompt_price = pricing.get(
                "prompt"
            )

            completion_price = pricing.get(
                "completion"
            )

            if not self._is_free_price(
                prompt_price
            ):
                continue

            if not self._is_free_price(
                completion_price
            ):
                continue

            candidates.append(
                model
            )

        # Prefer models with larger context windows.
        candidates.sort(
            key=lambda item: item.get(
                "context_length",
                0
            ),
            reverse=True,
        )

        # Keep fallback attempts reasonable.
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

            text_parts = []

            for item in content:

                if not isinstance(item, dict):
                    continue

                text = item.get("text")

                if text:
                    text_parts.append(text)

            answer = "\n".join(
                text_parts
            ).strip()

            if answer:
                return answer

        return None

    @staticmethod
    def _looks_like_safety_only_response(
        answer: str
    ):

        cleaned = answer.strip().lower()

        safety_only_responses = {
            "safe",
            "unsafe",
            "user safety: safe",
            "user safety: unsafe",
        }

        return cleaned in safety_only_responses
