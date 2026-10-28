"""
Runner Redaction Module
Comprehensive credential masking across console, logs, and reports.
"""

import re
from typing import Any, Iterable, List, Optional, Set

REPLACEMENT = "***"

FLAG_PATTERNS = [
    re.compile(r'(?:--token|--password|--psk-key|--psk-id)[\s=]+([^\s"\'`]+)', re.IGNORECASE),
]

BEARER_PATTERN = re.compile(r'(Bearer\s+)[^\s"\'`,}]+', re.IGNORECASE)
URL_TOKEN_PATTERN = re.compile(r'([?&]token=)[^&\s"\'`]+', re.IGNORECASE)


class Redactor:
    """
    Centralized credential masking engine.
    Extracts secrets from config.yaml, CLI arguments, and command lines,
    then replaces them with '***' in text, data structures, and command outputs.
    """

    def __init__(
        self,
        config: Optional[dict] = None,
        custom_args: Optional[Iterable[str]] = None,
        extra_secrets: Optional[Iterable[str]] = None,
    ):
        self.secrets: Set[str] = set()
        if config:
            self.add_from_config(config)
        if custom_args:
            for a in custom_args:
                self.add_from_arg_string(a)
        if extra_secrets:
            for s in extra_secrets:
                self.add_secret(s)

        # Include known simulator default secrets
        self.add_secret("test_token_ws")

    def add_secret(self, val: Any) -> None:
        """Adds a candidate secret to the redaction pool if string and length >= 4."""
        if isinstance(val, str):
            clean = val.strip()
            # Bỏ qua giá trị rỗng hoặc ngắn hơn 4 ký tự
            if len(clean) >= 4:
                self.secrets.add(clean)

    def add_from_config(self, cfg: Any) -> None:
        """
        Recursively extracts secrets from config dict/list:
        Any string value where the key contains 'token', 'password', 'secret', 'psk', or 'key'
        (excluding 'routing_key'). Also extracts Bearer tokens from strings.
        """
        sensitive_key_words = ("token", "password", "secret", "psk")

        def _walk(node: Any):
            if isinstance(node, dict):
                for k, v in node.items():
                    k_str = str(k).lower()
                    is_sensitive = any(w in k_str for w in sensitive_key_words) or (
                        "key" in k_str and "routing_key" not in k_str
                    )
                    if is_sensitive and isinstance(v, str):
                        self.add_secret(v)
                    if isinstance(v, str) and "bearer " in v.lower():
                        m = re.search(r'bearer\s+([^\s"\'`,}]+)', v, re.IGNORECASE)
                        if m:
                            self.add_secret(m.group(1))
                    _walk(v)
            elif isinstance(node, list):
                for item in node:
                    _walk(item)

        _walk(cfg)

    def add_from_arg_string(self, text: str) -> None:
        """Extracts secrets from user argument strings like --token CANARY_TOKEN."""
        if not text:
            return
        for pat in FLAG_PATTERNS:
            for m in pat.finditer(text):
                val = m.group(1).strip('"\',;')
                self.add_secret(val)

    def add_from_cmd(self, cmd: List[str]) -> None:
        """Extracts secrets from built command argument lists."""
        for i, arg in enumerate(cmd):
            for flag in ("--token", "--password", "--psk-key", "--psk-id"):
                if arg.lower() == flag and i + 1 < len(cmd):
                    self.add_secret(cmd[i + 1])
                elif arg.lower().startswith(flag + "="):
                    self.add_secret(arg[len(flag) + 1 :])

    def redact_text(self, text: str) -> str:
        """
        Replaces all credentials in text with '***'.
        Applies structural regex replacements (Bearer, URL tokens, CLI flags)
        and literal secret replacements (longest first).
        """
        if not text or not isinstance(text, str):
            return text

        # 1. Redact Bearer token value
        res = BEARER_PATTERN.sub(r'\g<1>' + REPLACEMENT, text)

        # 2. Redact URL token parameter (?token=... or &token=...)
        res = URL_TOKEN_PATTERN.sub(r'\g<1>' + REPLACEMENT, res)

        # 3. Redact CLI flags (--token=..., --password ..., etc.)
        for pat in FLAG_PATTERNS:
            res = pat.sub(lambda m: m.group(0).replace(m.group(1), REPLACEMENT), res)

        # 4. Redact collected secret literal values (sorted descending by length)
        sorted_secrets = sorted(self.secrets, key=len, reverse=True)
        for s in sorted_secrets:
            if s in res:
                res = res.replace(s, REPLACEMENT)

        return res

    def redact_data(self, data: Any) -> Any:
        """
        Recursively redacts dictionary, list, or primitive data structures.
        """
        if isinstance(data, dict):
            new_dict = {}
            for k, v in data.items():
                k_redacted = self.redact_text(str(k))
                new_dict[k_redacted] = self.redact_data(v)
            return new_dict
        elif isinstance(data, list):
            return [self.redact_data(x) for x in data]
        elif isinstance(data, tuple):
            return tuple(self.redact_data(x) for x in data)
        elif isinstance(data, str):
            return self.redact_text(data)
        else:
            return data

