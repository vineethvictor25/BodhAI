"""LangChain model connection owned by this application."""
import json

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from openai import APIConnectionError, APIStatusError, APITimeoutError,DefaultHttpxClient

from .config import ModelConfig
from .provider_errors import ProviderError, retry_delay
from .model_policy import ModelPolicy
from .value_codec import dumps as exact_dumps


class LangChainModel:
    def close(self):
        client=getattr(self.chat,'root_client',None)
        if client is not None:client.close()

    def __init__(self, model=None, *, config=None, chat=None,source='sample',egress_policy=None,principal_groups=()):
        self.source,self.egress_policy=source,egress_policy
        self.principal_groups=principal_groups
        self.settings = config or ModelConfig.load(model=model)
        settings = self.settings
        options = {"model": settings.model, "api_key": settings.api_key, "base_url": settings.base_url,
                   "timeout": settings.timeout, "max_tokens": settings.max_tokens, "max_retries": 0,
                   "stream_usage": False, "use_responses_api": settings.api_style == "responses",
                   "default_headers": {"User-Agent": "standalone-langgraph-nlq/1.0"}}
        if settings.api_style == "responses":
            options["store"] = False
        if chat is None:
            options['http_client']=DefaultHttpxClient(follow_redirects=False,trust_env=False)
        self.chat = chat if chat is not None else ChatOpenAI(**options)

    def generate(self, instructions, context, output_type):
        from .cancellation import check
        check()
        (self.egress_policy or ModelPolicy.load()).check(self.settings,self.source,context,instructions,self.principal_groups)
        options = {"tool_choice": output_type.__name__}
        if self.settings.strict_tools:
            options["strict"] = True
        if self.settings.send_parallel:
            options["parallel_tool_calls"] = False
        bound = self.chat.bind_tools([output_type], **options)
        try:
            from .telemetry import measured,usage
            with measured('provider'):
                reply = bound.invoke([SystemMessage(content=instructions),
                                      HumanMessage(content=exact_dumps(context))])
            usage(getattr(reply,'usage_metadata',None))
        except APIStatusError as exc:
            if exc.status_code == 429:
                raise ProviderError("Model provider rate limit reached (HTTP 429). Retry after the provider's limit resets or check your quota.",
                    "rate_limit",retry_delay(exc.response.headers)) from None
            if exc.status_code == 400 and getattr(exc, "code", None) in {
                "tool_use_failed", "tool_call_validation_error", "json_validate_failed"
            }:
                # A generated handoff can be malformed even when the request is valid.
                # Let the specialist retry it within the existing local/global budgets.
                raise ValueError("The provider generated an invalid tool handoff; regenerate it using the supplied schema") from None
            raise ProviderError(f"Model provider returned HTTP {exc.status_code}; check this application's provider settings and account",
                "transient" if exc.status_code in {408,500,502,503,504} else "configuration",retry_delay(exc.response.headers)) from None
        except (APIConnectionError, APITimeoutError):
            raise ProviderError("Cannot reach the configured model provider","connection") from None
        reason = reply.response_metadata.get("finish_reason")
        check()
        if reason in {"length", "content_filter"}:
            raise RuntimeError("The provider response was incomplete or filtered")
        calls = reply.tool_calls
        if reply.invalid_tool_calls or len(calls) != 1 or calls[0]["name"] != output_type.__name__:
            raise ValueError("Return exactly one call to the supplied handoff tool")
        return calls[0]["args"]
