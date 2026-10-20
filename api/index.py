# Vstupný bod pre Vercel (Python serverless funkcia) – len fronta úloh, slicuje PC.
import os

from server.app import create_app
from server.storage import GitHubStore, LocalStore

if os.environ.get("GITHUB_TOKEN") and os.environ.get("GITHUB_DATA_REPO"):
    _store = GitHubStore(
        os.environ["GITHUB_TOKEN"],
        os.environ["GITHUB_DATA_REPO"],
        os.environ.get("GITHUB_DATA_BRANCH", "main"),
        os.environ.get("GITHUB_DATA_PREFIX", "kompilator"),
    )
else:
    _store = LocalStore(os.environ.get("LOCAL_DATA", "/tmp/kompilator" if os.environ.get("VERCEL") else ".data"))

app = create_app(_store, mode="cloud")
