"""Git credential helper: credentials stay in the isolated child environment."""

import os
import sys

if __name__ == "__main__":
    prompt = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    print(
        os.environ.get("KBAGENT_GITLAB_USERNAME", "oauth2")
        if "username" in prompt
        else os.environ.get("KBAGENT_GITLAB_TOKEN", "")
    )
