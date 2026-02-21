from __future__ import annotations

from dataclasses import dataclass

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials


TOKEN_URI = "https://oauth2.googleapis.com/token"


@dataclass(frozen=True)
class GoogleOAuthSecrets:
    client_id: str
    client_secret: str
    refresh_token: str


def build_credentials(secrets: GoogleOAuthSecrets, *, scopes: list[str]) -> Credentials:
    creds = Credentials(
        token=None,
        refresh_token=secrets.refresh_token,
        token_uri=TOKEN_URI,
        client_id=secrets.client_id,
        client_secret=secrets.client_secret,
        scopes=scopes,
    )
    creds.refresh(Request())
    return creds

