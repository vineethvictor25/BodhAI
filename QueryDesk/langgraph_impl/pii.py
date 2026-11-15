"""Local field encryption and PII policy. Keys never enter graph state or prompts."""
import base64
import hashlib
import json
import os
import secrets
from pathlib import Path

from .config import APP_DIR
from .value_codec import dumps as exact_dumps,loads as exact_loads
from .pii_bounds import PIILimits,PIIBoundError,value_size

PREFIX="qdpii:v1:"


class LocalCipher:
    def decrypt_bounded(self,value,aad,limits):
        if isinstance(value,str) and value.startswith(PREFIX):
            if len(value)>limits.max_envelope_bytes:raise PIIBoundError('Encrypted PII cell exceeds its approved envelope limit.')
            try:raw=base64.b64decode(value[len(PREFIX):],altchars=b'-_',validate=True)
            except Exception:raise ValueError('PII authentication/decryption failed; check the key and configuration') from None
            # AES-GCM payload size is known before decrypting or parsing its JSON.
            if len(raw)>=28 and len(raw)-28>limits.max_cell_bytes:raise PIIBoundError('Encrypted PII cell exceeds its approved plaintext-cell limit.')
        elif isinstance(value,str) and len(value)>limits.max_cell_bytes:
            raise PIIBoundError('PII cell exceeds its approved plaintext-cell limit.')
        result=self.decrypt(value,aad)
        if value_size(result)>limits.max_cell_bytes:raise PIIBoundError('PII cell exceeds its approved plaintext-cell limit.')
        return result

    def __init__(self,key_file,enabled=False):
        self.enabled=enabled
        self.key=None
        path=Path(key_file)
        if path.is_file():
            self.key=base64.urlsafe_b64decode(path.read_bytes().strip())
            if len(self.key)!=32:
                raise ValueError("The PII key must contain a base64-encoded 256-bit key")
        if enabled and self.key is None:
            raise ValueError("PII encryption is enabled but its key file is missing. Initialize the key before starting.")

    def encrypt(self,value,aad):
        if self.key is None:
            raise ValueError("PII encryption key unavailable")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce=secrets.token_bytes(12)
        return PREFIX+base64.urlsafe_b64encode(nonce+AESGCM(self.key).encrypt(nonce,exact_dumps(value).encode(),aad.encode())).decode()

    def decrypt(self,value,aad):
        if not isinstance(value,str) or not value.startswith(PREFIX):
            return value
        if self.key is None:
            raise ValueError("An encrypted value cannot be read without its configured key")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        try:
            raw=base64.urlsafe_b64decode(value[len(PREFIX):])
            return exact_loads(AESGCM(self.key).decrypt(raw[:12],raw[12:],aad.encode()))
        except Exception:
            raise ValueError("PII authentication/decryption failed; check the key and configuration") from None


class PIIPolicy:
    def __init__(self,values):
        raw=str(values.get("PII_ENCRYPTION_ENABLED","false")).lower()
        if raw not in {"true","false","1","0"}:
            raise ValueError("PII_ENCRYPTION_ENABLED must be true or false")
        self.enabled=raw in {"true","1"}
        self.processing_limits=PIILimits.load(values)
        self.columns={"sample":{"customers":["name","phone","mobile","email"],"customer_profiles":["email"]}}
        self.key_file=APP_DIR/".secrets"/"pii.key"
        config={}
        if values.get("PII_CONFIG_FILE"):
            path=Path(values["PII_CONFIG_FILE"])
            if not path.is_absolute():
                path=APP_DIR/path
            config=json.loads(path.read_text(encoding="utf-8"))
            if set(config)-{"provider","key_file","sources"} or config.get("provider","aes-gcm")!="aes-gcm":
                raise ValueError("Only the registered local aes-gcm encryption provider is supported")
            self.key_file=Path(config.get("key_file",self.key_file))
            if not self.key_file.is_absolute():
                self.key_file=APP_DIR/self.key_file
            mappings=config.get("sources",{})
            if not isinstance(mappings,dict):
                raise ValueError("PII sources must map source IDs to table/column lists")
            for source,tables in mappings.items():
                if not isinstance(tables,dict) or any(not isinstance(cols,list) or any(not isinstance(c,str) or not c for c in cols) for cols in tables.values()):
                    raise ValueError("Invalid PII column mapping")
                self.columns[source]=tables
        self.cipher=LocalCipher(self.key_file,self.enabled)
        self.signature=hashlib.sha256(json.dumps({"enabled":self.enabled,"columns":self.columns,
            'processing_limits':self.processing_limits.payload(),
            "key_fingerprint":hashlib.sha256(self.cipher.key).hexdigest() if self.cipher.key else None},sort_keys=True).encode()).hexdigest()

    def for_source(self,source,can_view):
        if source not in self.columns and not can_view:
            raise PermissionError("An administrator must review this source's PII mapping before member access.")
        return self.columns.get(source,{})


def initialize_key(filename):
    path=Path(filename)
    path.parent.mkdir(parents=True,exist_ok=True)
    # Never replace an existing encryption key.
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,"wb") as output:
        output.write(base64.urlsafe_b64encode(secrets.token_bytes(32)))
        output.flush();os.fsync(output.fileno())


def field_aad(source,table,column,identity):
    return json.dumps(["field",source,table,column,identity],separators=(",",":"))
