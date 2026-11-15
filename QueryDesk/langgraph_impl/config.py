"""Configuration owned entirely by this application."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import dotenv_values

APP_DIR = Path(__file__).resolve().parent


def environment_values(env_file=None,environ=None):
    shell=dict(os.environ if environ is None else environ)
    filename=Path(env_file or shell.get('NLQ_ENV_FILE') or APP_DIR/'.env')
    if (env_file or shell.get('NLQ_ENV_FILE')) and not filename.is_file():
        raise ValueError('The specified environment file does not exist')
    return {**dict(dotenv_values(filename,interpolate=False)),**shell}


@dataclass(frozen=True)
class ModelConfig:
    model: str
    api_key: str = field(repr=False)
    base_url: str = "https://api.openai.com/v1"
    api_style: str = "chat_completions"
    strict_tools: bool = False
    max_tokens: int = 4000
    timeout: float = 90.0
    send_parallel: bool = True

    @classmethod
    def for_role(cls, role, model=None, env_file=None, environ=None):
        if role not in {'intent','schema','columns','sql','validation','response','visualization'}:
            raise ValueError('Unknown model role')
        values=environment_values(env_file,environ)
        prefix='LLM_'+role.upper()+'_'
        if values.get(prefix+'ENDPOINT') and not (values.get(prefix+'API_KEY') and values.get(prefix+'MODEL')):
            raise ValueError('A role endpoint requires its own API key and model')
        if values.get(prefix+'ENDPOINT') and prefix+'API_STYLE' not in values:
            values['LLM_API_STYLE']=''
        for name in ('ENDPOINT','API_KEY','MODEL','API_STYLE','STRICT_TOOLS','MAX_TOKENS','TIMEOUT_SECONDS','SEND_PARALLEL_TOOL_CALLS'):
            if values.get(prefix+name) is not None:
                values['LLM_'+name]=values[prefix+name]
        return cls.load(model=values.get(prefix+'MODEL') or model,env_file=env_file,environ=values)

    @classmethod
    def load(cls, model=None, env_file=None, environ=None):
        shell = dict(os.environ if environ is None else environ)
        filename = Path(env_file or shell.get("NLQ_ENV_FILE") or APP_DIR / ".env")
        if (env_file or shell.get("NLQ_ENV_FILE")) and not filename.is_file():
            raise ValueError("The specified environment file does not exist")
        values = {**dict(dotenv_values(filename, interpolate=False)), **shell}

        def flag(key, default):
            raw = str(values.get(key, str(default))).strip().lower()
            if raw not in {"true", "false", "1", "0"}:
                raise ValueError(f"{key} must be true or false")
            return raw in {"true", "1"}

        groq = bool(values.get("GROQ_MODEL") or values.get("GROQ_API_KEY"))
        address = (values.get("LLM_ENDPOINT") or values.get("LLM_BASE_URL") or
                   values.get("OPENAI_BASE_URL") or
                   ("https://api.groq.com/openai/v1" if groq else "https://api.openai.com/v1"))
        url = urlsplit(address.strip())
        if url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Configure an HTTP(S) model URL without credentials, query strings or fragments")
        path = url.path.rstrip("/")
        explicit_style = None
        for ending, kind in (("/chat/completions", "chat_completions"), ("/responses", "responses")):
            if path.endswith(ending):
                path = path[:-len(ending)]
                explicit_style = kind
                break
        style = values.get("LLM_API_STYLE") or explicit_style or "chat_completions"
        if style not in {"responses", "chat_completions"} or (explicit_style and style != explicit_style):
            raise ValueError("LLM_API_STYLE must match the configured model endpoint")
        is_groq = url.hostname == "api.groq.com"
        key = values.get("LLM_API_KEY", values.get("GROQ_API_KEY" if is_groq else "OPENAI_API_KEY", "")) or ""
        if not key.strip() or key in {"your-api-key", "replace-with-your-api-key"}:
            raise ValueError("Set LLM_API_KEY or GROQ_API_KEY in this application's .env")
        name = model or values.get("LLM_MODEL") or values.get("GROQ_MODEL" if is_groq else "OPENAI_MODEL")
        if not name or not name.strip():
            raise ValueError("Set LLM_MODEL or GROQ_MODEL in this application's .env")
        try:
            tokens = int(values.get("LLM_MAX_TOKENS", 4000))
            timeout = float(values.get("LLM_TIMEOUT_SECONDS", 90))
        except (ValueError, TypeError):
            raise ValueError("Invalid model token budget or timeout") from None
        if not 1 <= tokens <= 100000 or not 0 < timeout <= 600:
            raise ValueError("Model token budget or timeout is out of range")
        return cls(name.strip(), key.strip(), urlunsplit((url.scheme, url.netloc, path, "", "")),
                   style, flag("LLM_STRICT_TOOLS", False), tokens, timeout,
                   flag("LLM_SEND_PARALLEL_TOOL_CALLS", True))
