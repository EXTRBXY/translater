"""Prints pinned commit, size and sha256 of Hugging Face model files: python tools/hf_manifest.py owner/repo ..."""

import hashlib
import json
import sys
import urllib.request

_SKIP_PREFIXES = ("eole-model/", ".ipynb_checkpoints/")
_SKIP_SUFFIXES = (".md", ".gitattributes")


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


def _sha256(url: str) -> str:
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=600) as response:
        while chunk := response.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def manifest(repo: str) -> dict:
    info = _get_json(f"https://huggingface.co/api/models/{repo}?blobs=true")
    commit = info["sha"]
    files = []
    for sibling in info["siblings"]:
        name = sibling["rfilename"]
        if name.startswith(_SKIP_PREFIXES) or name.endswith(_SKIP_SUFFIXES):
            continue
        url = f"https://huggingface.co/{repo}/resolve/{commit}/{name}"
        lfs = sibling.get("lfs") or {}
        files.append({"path": name, "size": sibling.get("size"), "sha256": lfs.get("sha256") or _sha256(url)})
    license_ = (info.get("cardData") or {}).get("license")
    return {"repo": repo, "commit": commit, "license": license_, "files": files}


if __name__ == "__main__":
    print(json.dumps([manifest(repo) for repo in sys.argv[1:]], indent=2))
