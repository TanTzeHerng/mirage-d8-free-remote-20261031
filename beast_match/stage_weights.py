"""Reassemble and authenticate the exact user-supplied Beast.pb.gz on cloud runner.

Only AES-256-GCM encrypted chunks are hosted on the public GitHub release.
BEAST_AES256_HEX is a GitHub Actions repository secret, never printed.
"""
import hashlib
import json
import os
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT=Path(__file__).resolve().parent.parent
CIPHER=ROOT/"beast_encrypted"
OUTPUT=ROOT/"Beast.pb.gz"
ORIGINAL_SHA="394d1ca773b41e9f88f3993befd4a5c3a45eb6d7bcc5320a6ecca441625298cf"
ORIGINAL_SIZE=55165842
def sha256(x):
    return hashlib.sha256(x).hexdigest()
def main():
    manifest=json.loads((CIPHER/"manifest.json").read_text(encoding="utf8"))
    parts=manifest["parts"]
    if not 1<=len(parts)<=128:
        raise ValueError("Invalid encrypted Beast model part count")
    encrypted=bytearray()
    for i,entry in enumerate(parts):
        name="beast-aesgcm-%03d.part"%i
        if name != entry["name"]:
            raise ValueError("Unexpected chunk order or name")
        buf=(CIPHER/name).read_bytes()
        if len(buf)!=int(entry["size"]) or sha256(buf)!=entry["sha256"]:
            raise ValueError("Encrypted Beast chunk size/hash mismatch: "+name)
        encrypted.extend(buf)
    if len(encrypted)!=int(manifest["ciphertext_size"]) or sha256(encrypted)!=manifest["ciphertext_sha256"]:
        raise ValueError("Encrypted Beast model size/hash mismatch")
    key=bytes.fromhex(os.environ["BEAST_AES256_HEX"])
    if len(key)!=32:
        raise ValueError("GitHub secret BEAST_AES256_HEX is not a 256-bit key")
    decrypted=AESGCM(key).decrypt(bytes(encrypted[:12]),bytes(encrypted[12:]),None)
    if (len(decrypted)!=ORIGINAL_SIZE or sha256(decrypted)!=ORIGINAL_SHA or
            manifest.get("expected_plaintext_sha256")!=ORIGINAL_SHA):
        raise ValueError("Decrypted Beast model does not match supplied network")
    tmp=OUTPUT.with_suffix(".pb.gz.tmp")
    tmp.write_bytes(decrypted)
    tmp.replace(OUTPUT)
    print("ORIGINAL_BEAST_NETWORK_VERIFIED",len(decrypted),sha256(decrypted),flush=True)
if __name__=="__main__":main()
