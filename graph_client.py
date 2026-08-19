import os
import requests
from dotenv import load_dotenv

load_dotenv()

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class GraphConfigError(RuntimeError):
    """Raised when required environment variables are missing."""

class GraphClient:
    def __init__(self):
        self.tenant_id = os.environ.get("GRAPHTENANTID")
        self.client_id = os.environ.get("GRAPHCLIENTID")
        self.client_secret = os.environ.get("GRAPHCLIENTSECRET")
        # Optional fallback for local/single-user testing only. In the
        # deployed org-wide tool, the organizer is passed in per-call
        # instead (see user_id params below) so this app works for anyone
        # in the tenant, not just one hardcoded person.
        self.default_user_id = os.environ.get("GRAPHUSERID")

        missing = [
            name
            for name, val in [
                ("GRAPHTENANTID", self.tenant_id),
                ("GRAPHCLIENTID", self.client_id),
                ("GRAPHCLIENTSECRET", self.client_secret),
            ]
            if not val
        ]
        if missing:
            raise GraphConfigError(
                f"Missing required environment variables: {', '.join(missing)}. "
                "Set them in your shell or in a local .env file."
            )

        self._token = None
        self._headers = None
        self._object_id_cache = {}

    def _resolve_user_id(self, user_id: str = None) -> str:
        """Falls back to GRAPHUSERID only if no organizer was supplied.

        The onlineMeetings/recordings/transcripts endpoints require the
        organizer's directory object ID (a GUID) - unlike most /users/{id}
        Graph endpoints, they don't accept an email/UPN directly. So if what
        we have looks like an email, resolve it to the GUID first.
        """
        resolved = user_id or self.default_user_id
        if not resolved:
            raise ValueError(
                "No organizer specified. Pass user_id (the meeting organizer's "
                "email/UPN or object ID), or set GRAPHUSERID for a single-user setup."
            )
        if "@" in resolved:
            return self._resolve_object_id(resolved)
        return resolved

    def _resolve_object_id(self, email_or_upn: str) -> str:
        if email_or_upn in self._object_id_cache:
            return self._object_id_cache[email_or_upn]
        url = f"{GRAPH_BASE}/users/{email_or_upn}"
        resp = requests.get(url, headers=self.headers)
        resp.raise_for_status()
        object_id = resp.json()["id"]
        self._object_id_cache[email_or_upn] = object_id
        return object_id

    # Auth
    def _get_token(self) -> str:
        """Fetch a fresh app-only access token via the client credentials flow."""
        url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        body = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }
        resp = requests.post(url, data=body)
        resp.raise_for_status()
        return resp.json()["access_token"]

    @property
    def headers(self) -> dict:
        """Returns auth headers, fetching a new token on first use."""
        if self._headers is None:
            self._token = self._get_token()
            self._headers = {"Authorization": f"Bearer {self._token}"}
        return self._headers

    def refresh_token(self):
        """Force a new token fetch (e.g. if a call fails with 401)."""
        self._token = self._get_token()
        self._headers = {"Authorization": f"Bearer {self._token}"}

    # Meeting lookup
    def get_meeting_by_join_url(self, join_url: str, user_id: str = None) -> dict:
        user_id = self._resolve_user_id(user_id)
        escaped = join_url.replace("'", "''")
        url = (
            f"{GRAPH_BASE}/users/{user_id}/onlineMeetings"
            f"?$filter=JoinWebUrl eq '{escaped}'"
        )
        resp = requests.get(url, headers=self.headers)
        resp.raise_for_status()
        results = resp.json().get("value", [])
        if not results:
            raise ValueError(f"No meeting found for join URL: {join_url}")
        return results[0]

    # Recordings
    def list_recordings(self, meeting_id: str, user_id: str = None) -> list:
        user_id = self._resolve_user_id(user_id)
        url = f"{GRAPH_BASE}/users/{user_id}/onlineMeetings/{meeting_id}/recordings"
        resp = requests.get(url, headers=self.headers)
        resp.raise_for_status()
        return resp.json().get("value", [])

    def download_recording(self, meeting_id: str, recording_id: str, out_path: str, user_id: str = None):
        user_id = self._resolve_user_id(user_id)
        url = (
            f"{GRAPH_BASE}/users/{user_id}/onlineMeetings/{meeting_id}"
            f"/recordings/{recording_id}/content"
        )
        self._download(url, out_path)

    # Transcripts
    def list_transcripts(self, meeting_id: str, user_id: str = None) -> list:
        user_id = self._resolve_user_id(user_id)
        url = f"{GRAPH_BASE}/users/{user_id}/onlineMeetings/{meeting_id}/transcripts"
        resp = requests.get(url, headers=self.headers)
        resp.raise_for_status()
        return resp.json().get("value", [])

    def download_transcript(self, meeting_id: str, transcript_id: str, out_path: str, user_id: str = None):
        user_id = self._resolve_user_id(user_id)
        url = (
            f"{GRAPH_BASE}/users/{user_id}/onlineMeetings/{meeting_id}"
            f"/transcripts/{transcript_id}/content"
        )
        self._download(url, out_path, extra_headers={"Accept": "text/vtt"})

    # Internal helper
    def _download(self, url: str, out_path: str, extra_headers: dict = None):
        headers = {**self.headers, **(extra_headers or {})}
        resp = requests.get(url, headers=headers, stream=True)
        resp.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)


# Convenience end-to-end function
def fetch_meeting_artifacts(join_url: str, organizer_email: str = None, out_dir: str = ".") -> dict:
    client = GraphClient()

    meeting = client.get_meeting_by_join_url(join_url, user_id=organizer_email)
    meeting_id = meeting["id"]

    result = {
        "meeting_id": meeting_id,
        "subject": meeting.get("subject"),
        "recording_path": None,
        "transcript_path": None,
    }

    recordings = client.list_recordings(meeting_id, user_id=organizer_email)
    if recordings:
        rec = recordings[0]
        rec_path = os.path.join(out_dir, "recording.mp4")
        client.download_recording(meeting_id, rec["id"], rec_path, user_id=organizer_email)
        result["recording_path"] = rec_path

    transcripts = client.list_transcripts(meeting_id, user_id=organizer_email)
    if transcripts:
        t = transcripts[0]
        t_path = os.path.join(out_dir, "transcript.vtt")
        client.download_transcript(meeting_id, t["id"], t_path, user_id=organizer_email)
        result["transcript_path"] = t_path

    return result


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python graph_client.py <teams_join_url> [organizer_email] [output_dir]")
        sys.exit(1)

    join_url = sys.argv[1]
    organizer_email = sys.argv[2] if len(sys.argv) > 2 else None
    out_dir = sys.argv[3] if len(sys.argv) > 3 else "."

    print(f"Looking up meeting: {join_url}")
    summary = fetch_meeting_artifacts(join_url, organizer_email, out_dir)
    print("Done.")
    print(f"  Meeting ID: {summary['meeting_id']}")
    print(f"  Subject: {summary['subject']}")
    print(f"  Recording saved to: {summary['recording_path']}")
    print(f"  Transcript saved to: {summary['transcript_path']}")
