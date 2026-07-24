"""Cryptographic primitives for the runvard federation."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey


@dataclass(frozen=True)
class NodeIdentity:
    node_id: str
    public_key: str
    signing_key: str


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def canonical_json(payload) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def node_id_from_public_key(public_key: str) -> str:
    return hashlib.sha256(_b64decode(public_key)).hexdigest()[:32]


def _atomic_write(path: Path, value: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def create_or_load_identity(data_dir) -> NodeIdentity:
    root = Path(data_dir)
    private_path = root / "identity.key"
    public_path = root / "identity.json"
    if private_path.exists():
        seed_text = private_path.read_text(encoding="utf-8").strip()
        signing_key = SigningKey(_b64decode(seed_text))
    else:
        signing_key = SigningKey.generate()
        seed_text = _b64encode(bytes(signing_key))
        _atomic_write(private_path, seed_text + "\n", 0o600)
    os.chmod(private_path, 0o600)
    public_key = _b64encode(bytes(signing_key.verify_key))
    identity = NodeIdentity(
        node_id=node_id_from_public_key(public_key),
        public_key=public_key,
        signing_key=seed_text,
    )
    public_payload = canonical_json({
        "node_id": identity.node_id,
        "public_key": identity.public_key,
    })
    if not public_path.exists() or public_path.read_text(encoding="utf-8") != public_payload + "\n":
        _atomic_write(public_path, public_payload + "\n", 0o644)
    return identity


def sign_payload(identity: NodeIdentity, payload) -> str:
    key = SigningKey(_b64decode(identity.signing_key))
    signature = key.sign(canonical_json(payload).encode("utf-8")).signature
    return _b64encode(signature)


def verify_payload(public_key: str, payload, signature: str) -> bool:
    try:
        VerifyKey(_b64decode(public_key)).verify(
            canonical_json(payload).encode("utf-8"),
            _b64decode(signature),
        )
    except (BadSignatureError, ValueError, TypeError) as exc:
        raise ValueError("invalid signature") from exc
    return True
