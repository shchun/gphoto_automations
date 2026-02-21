from __future__ import annotations

import argparse
import json

from google_auth_oauthlib.flow import InstalledAppFlow


PHOTOS_SCOPE = "https://www.googleapis.com/auth/photoslibrary.readonly"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"


def main() -> int:
    p = argparse.ArgumentParser(description="Get Google OAuth refresh token (local helper).")
    p.add_argument("--client-secrets", required=True, help="Path to OAuth client JSON (desktop app).")
    args = p.parse_args()

    flow = InstalledAppFlow.from_client_secrets_file(args.client_secrets, scopes=[PHOTOS_SCOPE, DRIVE_SCOPE])
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline", include_granted_scopes="true")

    out = {
        "refresh_token": creds.refresh_token,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

