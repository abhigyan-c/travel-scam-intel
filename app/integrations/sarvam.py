import asyncio
import http.client
import json
import ssl

from pydantic import BaseModel, Field, SecretStr, ValidationError

MODEL = "sarvam-105b"


class Completion[T: BaseModel](BaseModel):
    data: T | None = None
    error: str | None = None
    model: str = MODEL
    requests: int = 0
    usage: dict[str, int] = Field(default_factory=dict)
    request_id: str | None = None


def _request(key: str, payload: dict, timeout: float):
    connection = http.client.HTTPSConnection(
        "api.sarvam.ai", timeout=timeout, context=ssl.create_default_context()
    )
    try:
        connection.request(
            "POST",
            "/v1/chat/completions",
            body=json.dumps(payload),
            headers={"api-subscription-key": key, "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        if response.status != 200:
            return response.status, None
        body = response.read(1_000_001)
        if len(body) > 1_000_000:
            raise ValueError("response_too_large")
        return response.status, json.loads(body)
    finally:
        connection.close()


class Sarvam:
    def __init__(self, api_key: SecretStr, *, timeout: float = 20, concurrency: int = 2):
        if not 0 < timeout <= 60 or not 1 <= concurrency <= 4:
            raise ValueError("invalid Sarvam limits")
        self.api_key, self.timeout = api_key, timeout
        self._semaphore = asyncio.Semaphore(concurrency)

    async def complete[T: BaseModel](
        self, *, system: str, user: str, schema: type[T]
    ) -> Completion[T]:
        key = self.api_key.get_secret_value().strip()
        if not key:
            return Completion(error="missing_api_key")
        if not system.strip() or len(system) > 16000 or len(user) > 32000:
            raise ValueError("invalid prompt size")
        payload = {
            "model": MODEL,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "reasoning_effort": None,
            "max_tokens": 1024,
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": schema.model_json_schema(),
                },
            },
        }
        usage = {}
        request_id = None
        requests = 0
        try:
            async with asyncio.timeout(self.timeout):
                async with self._semaphore:
                    requests = 1
                    status, body = await asyncio.to_thread(_request, key, payload, self.timeout)
            if status != 200:
                error = {401: "authentication", 403: "authentication", 429: "rate_limited"}.get(
                    status, "provider_http_error"
                )
                return Completion(error=error, requests=1)
            if not isinstance(body, dict):
                raise ValueError("invalid envelope")
            raw_usage = body.get("usage") or {}
            if isinstance(raw_usage, dict):
                usage = {
                    name: value
                    for name, value in raw_usage.items()
                    if name in {"prompt_tokens", "completion_tokens", "total_tokens"}
                    and type(value) is int
                    and value >= 0
                }
            request_id = body.get("id") if isinstance(body.get("id"), str) else None
            if body.get("model") != MODEL:
                raise ValueError("unexpected model")
            choices = body.get("choices")
            if (
                not isinstance(choices, list)
                or len(choices) != 1
                or not isinstance(choices[0], dict)
            ):
                raise ValueError("invalid choices")
            choice = choices[0]
            message = choice.get("message")
            if not isinstance(message, dict):
                raise ValueError("invalid message")
            if message.get("refusal") or choice.get("finish_reason") == "content_filter":
                return Completion(error="refused", requests=1, usage=usage, request_id=request_id)
            if choice.get("finish_reason") != "stop":
                return Completion(
                    error="incomplete", requests=1, usage=usage, request_id=request_id
                )
            content = message.get("content")
            if not isinstance(content, str):
                raise ValueError("missing content")
            data = schema.model_validate_json(content)
            return Completion(data=data, requests=1, usage=usage, request_id=request_id)
        except TimeoutError:
            error = "timeout"
        except (OSError, http.client.HTTPException):
            error = "provider_unavailable"
        except (ValueError, TypeError, ValidationError):
            error = "invalid_response"
        return Completion(error=error, requests=requests, usage=usage, request_id=request_id)
