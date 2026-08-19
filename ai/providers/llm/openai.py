"""
LLM Provider — OpenAI / DeepSeek 桩。

复用现有 core/script_gen.py（已通过 openai 客户端调 DeepSeek）。
"""

import json

from ..base import AIProvider, ProviderDomain, TaskRequest, TaskResult, TaskHandle, TaskStatus


def _message_text(content) -> str:
    """Normalize OpenAI-compatible string, object and content-part replies."""
    if isinstance(content, str):
        return content
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    if isinstance(content, dict):
        if set(content).intersection({"type", "text", "content"}):
            value = content.get("text") or content.get("content") or ""
            if isinstance(value, dict):
                value = value.get("value") or value.get("text") or ""
            return str(value or "")
        return json.dumps(content, ensure_ascii=False)
    if isinstance(content, (list, tuple)):
        pieces = []
        for part in content:
            if isinstance(part, dict):
                value = part.get("text") or part.get("content") or ""
                if isinstance(value, dict):
                    value = value.get("value") or value.get("text") or ""
            else:
                value = getattr(part, "text", None)
                if isinstance(value, dict):
                    value = value.get("value") or value.get("text") or ""
            if value:
                pieces.append(str(value))
        return "".join(pieces)
    return str(content or "")


class LLMProvider(AIProvider):
    """大语言模型 Provider 基类。"""
    domain = ProviderDomain.LLM


class OpenAIProvider(LLMProvider):
    """OpenAI GPT 系列（支持官方及 OpenAI 兼容网关）。"""
    name = "openai"
    capabilities = ["chat", "json"]

    def execute(self, request: TaskRequest) -> TaskHandle:
        try:
            import openai
            kwargs = {
                "api_key": self.api_key,
            }
            # Preserve the SDK's original timeout/retry behavior unless a
            # non-storyboard caller explicitly asks for a custom timeout.
            if request.params.get("timeout_seconds") is not None:
                kwargs["timeout"] = max(30.0, min(
                    600.0, float(request.params["timeout_seconds"])))
            nested = self.config.get("config")
            base_url = (self.config.get("base_url") or
                        (nested.get("base_url") if isinstance(nested, dict) else ""))
            if base_url:
                kwargs["base_url"] = base_url
            client = openai.OpenAI(**kwargs)
            messages = request.inputs.get("messages", [])
            model = request.params.get("model", "gpt-4o")
            completion = {"model": model, "messages": messages}
            for key in (
                    "temperature", "top_p", "max_tokens",
                    "max_completion_tokens", "response_format", "seed", "stop"):
                if key in request.params and request.params[key] is not None:
                    completion[key] = request.params[key]
            try:
                resp = client.chat.completions.create(**completion)
            except Exception as first_error:
                # OpenAI-compatible gateways differ on optional chat fields.
                # Remove only fields explicitly rejected as unsupported, then
                # retry once; this is a compatibility correction, not a
                # transient network retry.
                lowered = str(first_error).lower()
                removable = [key for key in (
                    "response_format", "temperature", "max_completion_tokens")
                    if key in completion and key.lower() in lowered]
                unsupported = any(token in lowered for token in (
                    "unsupported", "not support", "unknown parameter",
                    "unrecognized", "invalid parameter"))
                if not removable or not unsupported:
                    raise
                for key in removable:
                    completion.pop(key, None)
                resp = client.chat.completions.create(**completion)
            choice = resp.choices[0]
            message = choice.message
            content = _message_text(getattr(message, "content", ""))
            finish_reason = str(getattr(choice, "finish_reason", "") or "")
            refusal = str(getattr(message, "refusal", "") or "")
            if not content.strip():
                detail = refusal or finish_reason or "unknown"
                raise RuntimeError(f"模型返回空内容（finish_reason={detail}）")
            h = TaskHandle(id=f"openai_{request.to_cache_key()[:8]}",
                           provider_name=self.name, operation=request.operation,
                           status=TaskStatus.DONE, progress=1.0)
            usage = getattr(resp, "usage", None)
            h.result = TaskResult(
                success=True,
                data=content,
                provider_raw={
                    "response_id": str(getattr(resp, "id", "") or ""),
                    "model": str(getattr(resp, "model", model) or model),
                    "finish_reason": finish_reason,
                    "content_chars": len(content),
                    "usage": (usage.model_dump() if hasattr(usage, "model_dump")
                              else {}),
                },
            )
            h.finished_at = __import__("time").time()
            return h
        except Exception as e:
            h = TaskHandle(id=f"openai_{request.to_cache_key()[:8]}",
                           provider_name=self.name, operation=request.operation,
                           status=TaskStatus.FAILED)
            h.result = TaskResult(success=False, error=str(e))
            h.finished_at = __import__("time").time()
            return h


class DeepSeekProvider(LLMProvider):
    """DeepSeek（已可用，复用现有 core/script_gen.py）。"""
    name = "deepseek"
    capabilities = ["chat", "json"]

    def execute(self, request: TaskRequest) -> TaskHandle:
        try:
            import openai
            client = openai.OpenAI(
                api_key=self.api_key,
                base_url=self.config.get("base_url", "https://api.deepseek.com/v1"),
            )
            messages = request.inputs.get("messages", [])
            model = request.params.get("model", "deepseek-chat")
            resp = client.chat.completions.create(model=model, messages=messages)
            choice = resp.choices[0]
            content = _message_text(choice.message.content)
            if not content.strip():
                raise RuntimeError(
                    "模型返回空内容（finish_reason=" +
                    str(getattr(choice, "finish_reason", "unknown") or "unknown") + ")")
            h = TaskHandle(id=f"deepseek_{request.to_cache_key()[:8]}",
                           provider_name=self.name, operation=request.operation,
                           status=TaskStatus.DONE, progress=1.0)
            h.result = TaskResult(success=True, data=content)
            h.finished_at = __import__("time").time()
            return h
        except Exception as e:
            h = TaskHandle(id=f"deepseek_{request.to_cache_key()[:8]}",
                           provider_name=self.name, operation=request.operation,
                           status=TaskStatus.FAILED)
            h.result = TaskResult(success=False, error=str(e))
            h.finished_at = __import__("time").time()
            return h
