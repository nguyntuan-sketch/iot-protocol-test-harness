"""
Runner Redaction Module
Comprehensive credential masking across console, logs, and reports.
"""

import os
import re
from typing import Any, Iterable, List, Optional, Set

REPLACEMENT = "***"

FLAG_PATTERNS = [
    re.compile(r'(?:--token|--password|--psk-key|--psk-id|--header-value)[\s=]+["\']?([^"\'`\s]+)["\']?', re.IGNORECASE),
    re.compile(r'(--header[\s=]+["\']?[^"\'`=\s]+)=([^"\'`\s]+)["\']?', re.IGNORECASE),
]

CONTEXTUAL_USERNAME_PATTERNS = [
    re.compile(r'(?:--username)[\s=]+["\']?([^"\'`\s]+)["\']?', re.IGNORECASE),
    re.compile(r'\b(?:Username|User)\s*[:=]\s*["\']?([^"\'`,}\s]+)["\']?', re.IGNORECASE),
]

BEARER_PATTERN = re.compile(r'(Bearer\s+)[^\s"\'`,}]+', re.IGNORECASE)
URL_TOKEN_PATTERN = re.compile(r'([?&]token=)[^&\s"\'`]+', re.IGNORECASE)

USERNAME_DEFAULTS = frozenset({
    "anonymous", "guest", "sim_device_001", "admin", "test",
})


class Redactor:
    """
    Centralized credential masking engine.
    Extracts secrets from config.yaml, config.local.yaml, CLI arguments,
    environment variables, password files, and command lines, then replaces
    them with '***' in text, data structures, and command outputs.
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

        # Automatically check for config.local.yaml in standard locations
        try:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            local_cfg_path = os.path.join(base_dir, "config", "config.local.yaml")
            if os.path.isfile(local_cfg_path):
                import yaml
                with open(local_cfg_path, "r", encoding="utf-8") as lf:
                    loc = yaml.safe_load(lf)
                    if loc:
                        self.add_from_config(loc)
        except Exception:
            pass

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
            clean = val.strip('"\',;` ')
            # Bỏ qua giá trị rỗng hoặc ngắn hơn 4 ký tự
            if len(clean) >= 4:
                self.secrets.add(clean)

    def add_from_config(self, cfg: Any) -> None:
        """
        Recursively extracts secrets from config dict/list:
        Any string value where the key contains 'token', 'password', 'secret', 'psk', 'key',
        or 'header_value' (excluding 'routing_key' and 'header_key').
        Also extracts Bearer tokens from strings, and checks password_file/env vars.

        Additional rules:
        - Any dict or list nested inside an 'auth' block: ALL string values are treated
          as secrets recursively, regardless of key names (covers extra_headers and
          any future nested auth dicts).
        - 'username'/'user' values in adapter configs are treated as secrets (full '***'
          masking) unless they are common non-sensitive defaults (anonymous, guest, etc.).
        """
        sensitive_key_words = ("token", "password", "secret", "psk", "header_value")
        _USERNAME_DEFAULTS = frozenset({
            "anonymous", "guest", "sim_device_001", "admin", "test",
        })

        def _collect_nested_secrets(target: Any):
            """Recursively adds all string values in a nested structure as secrets."""
            if isinstance(target, str):
                self.add_secret(target)
            elif isinstance(target, dict):
                for subv in target.values():
                    _collect_nested_secrets(subv)
            elif isinstance(target, list):
                for item in target:
                    _collect_nested_secrets(item)

        def _walk(node: Any, inside_auth: bool = False):
            if isinstance(node, dict):
                for k, v in node.items():
                    k_str = str(k).lower()

                    # Fix 2a: When inside an 'auth' block, any nested dict or list
                    # (e.g. extra_headers or future nested dicts) has ALL its string
                    # values treated as secrets recursively.
                    if inside_auth and isinstance(v, (dict, list)):
                        _collect_nested_secrets(v)
                        continue

                    # Direct field sensitivity check
                    is_sensitive = any(w in k_str for w in sensitive_key_words) or (
                        "key" in k_str and "routing_key" not in k_str and "header_key" not in k_str
                    )
                    if is_sensitive and isinstance(v, str):
                        self.add_secret(v)

                    # Fix 2b: Contextual username check for account-based protocols
                    # (ftp, mqtt, amqp) or whenever username is defined in config.
                    if k_str in ("username", "user") and isinstance(v, str):
                        if v.strip().lower() not in _USERNAME_DEFAULTS:
                            self.add_secret(v)

                    if isinstance(v, str) and "bearer " in v.lower():
                        m = re.search(r'bearer\s+([^\s"\'`,}]+)', v, re.IGNORECASE)
                        if m:
                            self.add_secret(m.group(1))

                    # Check if node defines a password file or env var
                    if "password_file" in k_str and isinstance(v, str) and v.strip():
                        fpath = v.strip()
                        if os.path.isfile(fpath):
                            try:
                                with open(fpath, "r", encoding="utf-8", errors="replace") as pf:
                                    self.add_secret(pf.read().strip())
                            except Exception:
                                pass
                    if any(e_name in k_str for e_name in ("env", "_env")) and isinstance(v, str) and v.strip():
                        env_val = os.environ.get(v.strip())
                        if env_val and (k_str not in ("username_env", "user_env") or env_val.strip().lower() not in _USERNAME_DEFAULTS):
                            self.add_secret(env_val)

                    _walk(v, inside_auth=(k_str == "auth"))
            elif isinstance(node, list):
                for item in node:
                    _walk(item, inside_auth=inside_auth)

        _walk(cfg)

    def add_from_arg_string(self, text: str) -> None:
        """Extracts secrets from user argument strings like --token EXAMPLE_TOKEN or --header KEY=VAL."""
        if not text:
            return
        for pat in FLAG_PATTERNS:
            for m in pat.finditer(text):
                val = m.group(1 if len(m.groups()) == 1 else 2).strip('"\',;` ')
                self.add_secret(val)

        # Contextual username CLI argument
        for m in re.finditer(r'(?:--username)[\s=]+["\']?([^"\'`\s]+)["\']?', text, re.IGNORECASE):
            u = m.group(1).strip('"\',;` ')
            if u.lower() not in USERNAME_DEFAULTS:
                self.add_secret(u)

        for m in re.finditer(r'--password-file[\s=]+["\']?([^"\'`\s]+)["\']?', text, re.IGNORECASE):
            fpath = m.group(1).strip()
            if os.path.isfile(fpath):
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as pf:
                        self.add_secret(pf.read().strip())
                except Exception:
                    pass

        for m in re.finditer(r'(?:--password-env|--header-value-env|--username-env)[\s=]+["\']?([^"\'`\s]+)["\']?', text, re.IGNORECASE):
            var_name = m.group(1).strip()
            env_val = os.environ.get(var_name)
            if env_val:
                if "username" in m.group(0).lower() and env_val.lower() in USERNAME_DEFAULTS:
                    continue
                self.add_secret(env_val)

    def add_from_cmd(self, cmd: List[str]) -> None:
        """Extracts secrets from built command argument lists."""
        for i, arg in enumerate(cmd):
            arg_lower = arg.lower()
            for flag in ("--token", "--password", "--psk-key", "--psk-id", "--header-value", "--username"):
                if arg_lower == flag and i + 1 < len(cmd):
                    val = cmd[i + 1].strip('"\',;` ')
                    if flag != "--username" or val.lower() not in USERNAME_DEFAULTS:
                        self.add_secret(val)
                elif arg_lower.startswith(flag + "="):
                    val = arg[len(flag) + 1 :].strip('"\',;` ')
                    if flag != "--username" or val.lower() not in USERNAME_DEFAULTS:
                        self.add_secret(val)

            # Header key=val
            if arg_lower == "--header" and i + 1 < len(cmd):
                val_part = cmd[i + 1]
                if "=" in val_part:
                    _, hval = val_part.split("=", 1)
                    self.add_secret(hval.strip('"\'` '))
            elif arg_lower.startswith("--header="):
                val_part = arg[9:]
                if "=" in val_part:
                    _, hval = val_part.split("=", 1)
                    self.add_secret(hval.strip('"\'` '))

            # Password file
            if arg_lower == "--password-file" and i + 1 < len(cmd):
                fpath = cmd[i + 1].strip('"\'` ')
                if os.path.isfile(fpath):
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as pf:
                            self.add_secret(pf.read().strip())
                    except Exception:
                        pass
            elif arg_lower.startswith("--password-file="):
                fpath = arg[16:].strip('"\'` ')
                if os.path.isfile(fpath):
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as pf:
                            self.add_secret(pf.read().strip())
                    except Exception:
                        pass

            # Environment variables
            for env_flag in ("--password-env", "--header-value-env", "--username-env"):
                if arg_lower == env_flag and i + 1 < len(cmd):
                    var_name = cmd[i + 1].strip('"\'` ')
                    env_val = os.environ.get(var_name)
                    if env_val and (env_flag != "--username-env" or env_val.lower() not in USERNAME_DEFAULTS):
                        self.add_secret(env_val)
                elif arg_lower.startswith(env_flag + "="):
                    var_name = arg[len(env_flag) + 1 :].strip('"\'` ')
                    env_val = os.environ.get(var_name)
                    if env_val and (env_flag != "--username-env" or env_val.lower() not in USERNAME_DEFAULTS):
                        self.add_secret(env_val)

    def redact_text(self, text: str) -> str:
        """
        Replaces all credentials in text with '***'.
        Applies structural regex replacements (Bearer, URL tokens, CLI flags, contextual usernames)
        and literal secret replacements (longest first).
        """
        if not text or not isinstance(text, str):
            return text

        # 1. Redact Bearer token value
        res = BEARER_PATTERN.sub(r'\g<1>' + REPLACEMENT, text)

        # 2. Redact URL token parameter (?token=... or &token=...)
        res = URL_TOKEN_PATTERN.sub(r'\g<1>' + REPLACEMENT, res)

        # 3. Redact CLI flags (--token=..., --password ..., --header-value ..., --header K=V)
        for pat in FLAG_PATTERNS:
            res = pat.sub(lambda m: m.group(0).replace(m.group(1 if len(m.groups()) == 1 else 2), REPLACEMENT), res)

        # 4. Contextual Username redaction (--username <val>, Username: <val>, User: <val>)
        for pat in CONTEXTUAL_USERNAME_PATTERNS:
            res = pat.sub(lambda m: m.group(0).replace(m.group(1), REPLACEMENT), res)

        # 5. Redact collected secret literal values (sorted descending by length)
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
                k_str = str(k).lower()
                k_redacted = self.redact_text(str(k))
                if any(w in k_str for w in ("password", "secret", "token", "psk", "header_value")) and isinstance(v, str) and v and v != "(none)":
                    new_dict[k_redacted] = REPLACEMENT
                elif k_str in ("user", "username") and isinstance(v, str) and v.strip().lower() not in USERNAME_DEFAULTS and v and v != "(none)":
                    new_dict[k_redacted] = REPLACEMENT
                else:
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

