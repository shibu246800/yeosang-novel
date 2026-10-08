import base64

import requests

from ai.providers import AIProvider


class GeminiProvider(AIProvider):
    """Google Gemini provider using the Interactions API."""

    name = "Gemini"

    MODELS = [
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash",
    ]

    INTERACTIONS_URL = (
        "https://generativelanguage.googleapis.com/"
        "v1beta/interactions"
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

        encoded_image = base64.b64encode(
            image_bytes
        ).decode("utf-8")

        last_error = None

        for model in self.MODELS:

            payload = {
                "model": model,
                "input": [
                    {
                        "type": "user_input",
                        "content": [
                            {
                                "type": "text",
                                "text": prompt,
                            },
                            {
                                "type": "image",
                                "data": encoded_image,
                                "mime_type": mime_type,
                            },
                        ],
                    }
                ],
            }

            try:

                print(
                    f"🧠 Trying Gemini model: {model}"
                )

                response = requests.post(
                    self.INTERACTIONS_URL,
                    headers={
                        "x-goog-api-key": self.api_key,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=180,
                )

                print(
                    f"📡 Gemini status: "
                    f"{response.status_code}"
                )

                if not response.ok:

                    print(
                        f"❌ Gemini {model}: "
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

                if answer:

                    print(
                        f"✅ Gemini succeeded: {model}"
                    )

                    return answer

                last_error = (
                    f"{model}: empty response"
                )

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
                    f"❌ Gemini request error: {error}"
                )

            except ValueError as error:

                last_error = (
                    f"{model}: invalid JSON"
                )

                print(
                    f"❌ Invalid Gemini JSON: {error}"
                )

        raise RuntimeError(
            last_error
            or "All Gemini models failed."
        )

    # ═════════════════════════════════════
    # TEXT GENERATION
    # ═════════════════════════════════════

    def generate_text(
        self,
        prompt: str,
    ) -> str:

        last_error = None

        for model in self.MODELS:

            payload = {
                "model": model,
                "input": [
                    {
                        "type": "user_input",
                        "content": [
                            {
                                "type": "text",
                                "text": prompt,
                            }
                        ],
                    }
                ],
            }

            try:

                print(
                    f"🧠 Gemini text model: {model}"
                )

                response = requests.post(
                    self.INTERACTIONS_URL,
                    headers={
                        "x-goog-api-key": self.api_key,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=180,
                )

                print(
                    f"📡 Gemini text status: "
                    f"{response.status_code}"
                )

                if not response.ok:

                    print(
                        f"❌ Gemini text {model}: "
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

                if answer:

                    print(
                        f"✅ Gemini text succeeded: "
                        f"{model}"
                    )

                    return answer

                last_error = (
                    f"{model}: empty response"
                )

            except requests.exceptions.Timeout:

                last_error = (
                    f"{model}: request timed out"
                )

            except requests.exceptions.RequestException as error:

                last_error = (
                    f"{model}: {error}"
                )

            except ValueError as error:

                last_error = (
                    f"{model}: invalid JSON"
                )

        raise RuntimeError(
            last_error
            or "All Gemini text models failed."
        )

    # ═════════════════════════════════════
    # OUTPUT EXTRACTION
    # ═════════════════════════════════════

    @staticmethod
    def _extract_output(data):

        output_text = data.get(
            "output_text"
        )

        if isinstance(output_text, str):

            output_text = output_text.strip()

            if output_text:
                return output_text

        steps = data.get(
            "steps",
            []
        )

        text_parts = []

        for step in steps:

            if step.get("type") != "model_output":
                continue

            content = step.get(
                "content",
                []
            )

            for item in content:

                if not isinstance(item, dict):
                    continue

                if item.get("type") != "text":
                    continue

                text = item.get("text")

                if text:
                    text_parts.append(text)

        answer = "\n".join(
            text_parts
        ).strip()

        return answer
