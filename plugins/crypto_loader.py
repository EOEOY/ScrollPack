import os
import sys
import json
import hashlib
import importlib.util
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend

_AES_KEY_HEX = "732ec76b49b48d043b4a826afc997394579975e2bc7a4209a8c1d5bc5708e333"
_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA5dyiMfFXAogMitdPs7ON
kYbs5BCrnk7Pv4fs9epqCRkD03lthJho5L/OVsMjEKox3PMnhNC0zpWR9pcExBLh
8ruXMOBWKeWWEVyWjzc1eU05RlAUhTSetpge0gGStG3a9cakbin4VlzNvOh7VCav
iEr/2THO94puxyTbHgjqzi3leZhDyvKG8tSK/vY9J8VD37mQBn+eGG1Rsb1Y4aWq
8RD+i/3VLKV+3uHv0QOGL5dwhZks+JCJ+lwEQ276blbsiU6cGZqlpNKdjAkixzM8
fxJBeKUf5zWxW8Guin3u/1PAZDnWYvaBpwk1qveyMFlVPpzq/BU6MkVFTSCR7TLf
eQIDAQAB
-----END PUBLIC KEY-----"""

_aes_key = bytes.fromhex(_AES_KEY_HEX)
_pub_key = serialization.load_pem_public_key(_PUBLIC_KEY_PEM, backend=default_backend())


def load_encrypted_plugin(plugin_dir, pkg, module_name):
    checksum_path = os.path.join(plugin_dir, "checksums.json")
    sig_path = os.path.join(plugin_dir, "checksums.sig")

    if os.path.exists(checksum_path) and os.path.exists(sig_path):
        with open(checksum_path, "rb") as f:
            checksums_raw = f.read()
        with open(sig_path, "rb") as f:
            signature = f.read()
        _pub_key.verify(
            signature, checksums_raw,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256(),
        )

    for fname in os.listdir(plugin_dir):
        if not fname.endswith(".enc"):
            continue
        enc_path = os.path.join(plugin_dir, fname)
        with open(enc_path, "rb") as f:
            data = f.read()
        nonce, ciphertext = data[:12], data[12:]
        aesgcm = AESGCM(_aes_key)
        source = aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")

        mod_name = fname[:-4]
        full_name = f"plugins.{pkg}.{mod_name}"
        spec = importlib.util.spec_from_loader(full_name, loader=None, origin=enc_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full_name] = mod
        exec(source, mod.__dict__)

    return sys.modules.get(f"plugins.{pkg}.{module_name}")
