import base64

import requests

from ai.providers import AIProvider


class GeminiProvider(AIProvider):
    """Google Gemini provider."""

    name = "Gemini"

    MODELS = [
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    ]

    BASE_URL = (
        "https://generativelanguage.googleapis.com/"
        "v1beta/models"
    )

    def __init__(self, api_key: str):
        self.api_key = api_key

    def analyze_image(
        self,
        image_bytes: bytes,
        mime_type: str,
        prompt: str,
    ) -> str:

        encoded_image = base64.b64encode(
            image_bytes
        ).decode("utf-8")

        last_error = None

        for model in self.MODELS:

            url = (
                f"{self.BASE_URL}/"
                f"{model}:generateContent"
            )

            payload = {
                "contents": [
                    {
                        "parts": [
                            {
                                "text": prompt
                            },
                            {
                                "inline_data": {
                                    "mime_type": mime_type,
                                    "data": encoded_image,
                                }
                            }
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": 0.9,
                    "maxOutputTokens": 5000,
                },
            }

            try:

                response = requests.post(
                    url,
                    params={
                        "key": self.api_key
                    },
                    headers={
                        "Content-Type": (
                            "application/json"
                        )
                    },
                    json=payload,
                    timeout=120,
                )

                if not response.ok:

                    last_error = (
                        f"{model}: "
                        f"HTTP {response.status_code} "
                        f"{response.text}"
                    )

                    continue

                data = response.json()

                candidates = data.get(
                    "candidates",
                    []
                )

                if not candidates:

                    last_error = (
                        f"{model}: "
                        "no candidates returned"
                    )

                    continue

                parts = (
                    candidates[0]
                    .get("content", {})
                    .get("parts", [])
                )

                text_parts = []

                for part in parts:

                    text = part.get("text")

                    if text:
                        text_parts.append(text)

                answer = "\n".join(
                    text_parts
                ).strip()

                if answer:

                    print(
                        f"✅ Gemini succeeded: "
                        f"{model}"
                    )

                    return answer

                last_error = (
                    f"{model}: empty response"
                )

            except requests.RequestException as error:

                last_error = (
                    f"{model}: {error}"
                )

        raise RuntimeError(
            last_error
            or "All Gemini models failed."
                    )
