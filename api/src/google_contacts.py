"""Minimal Google People API client for pushing candidate contacts into the
shared careers@ndlreno.com Google account.

Uses a pre-generated OAuth refresh token (see Governance/03_NDL_Hiring_Onboarding_Automation.md
for how it's produced) rather than the google-api-python-client SDK, so no
extra dependency is needed beyond `requests`, which is already used elsewhere
in this codebase.
"""
import os
import requests
from typing import Dict, List, Optional

TOKEN_URL = "https://oauth2.googleapis.com/token"
PEOPLE_API_BASE = "https://people.googleapis.com/v1"


class GoogleContactsClient:
    def __init__(
        self,
        client_id: str = None,
        client_secret: str = None,
        refresh_token: str = None,
    ):
        self.client_id = client_id or os.getenv("GOOGLE_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("GOOGLE_CLIENT_SECRET")
        self.refresh_token = refresh_token or os.getenv("GOOGLE_REFRESH_TOKEN")
        if not all([self.client_id, self.client_secret, self.refresh_token]):
            raise ValueError(
                "GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_REFRESH_TOKEN must be set"
            )
        self._access_token = None

    def _get_access_token(self) -> str:
        """Exchange the refresh token for a short-lived access token."""
        response = requests.post(
            TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        response.raise_for_status()
        return response.json()["access_token"]

    def _headers(self) -> Dict:
        if not self._access_token:
            self._access_token = self._get_access_token()
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    def search_contact(self, query: str) -> Optional[Dict]:
        """Search existing contacts by phone/email/name to avoid duplicates."""
        try:
            url = f"{PEOPLE_API_BASE}/people:searchContacts"
            params = {
                "query": query,
                "readMask": "names,phoneNumbers,emailAddresses",
            }
            response = requests.get(url, headers=self._headers(), params=params)
            response.raise_for_status()
            results = response.json().get("results", [])
            return results[0]["person"] if results else None
        except Exception:
            return None

    def get_or_create_group(self, group_name: str) -> str:
        """Return the resourceName of a contact group, creating it if needed."""
        url = f"{PEOPLE_API_BASE}/contactGroups"
        response = requests.get(url, headers=self._headers(), params={"pageSize": 200})
        response.raise_for_status()
        for group in response.json().get("contactGroups", []):
            if group.get("name", "").strip().lower() == group_name.strip().lower():
                return group["resourceName"]

        create_response = requests.post(
            url,
            headers=self._headers(),
            json={"contactGroup": {"name": group_name}},
        )
        create_response.raise_for_status()
        return create_response.json()["resourceName"]

    def upsert_contact(
        self,
        first_name: str,
        last_name: str,
        phone: str = None,
        email: str = None,
        existing_resource_name: str = None,
        group_resource_name: str = None,
    ) -> Dict:
        """Create a contact, or update it in place if a resourceName is already known."""
        person_fields = {
            "names": [{"givenName": first_name, "familyName": last_name}],
        }
        if phone:
            person_fields["phoneNumbers"] = [{"value": phone}]
        if email:
            person_fields["emailAddresses"] = [{"value": email}]
        if group_resource_name:
            person_fields["memberships"] = [
                {"contactGroupMembership": {"contactGroupResourceName": group_resource_name}}
            ]

        if existing_resource_name:
            update_mask = ",".join(person_fields.keys())
            url = f"{PEOPLE_API_BASE}/{existing_resource_name}:updateContact"
            response = requests.patch(
                url,
                headers=self._headers(),
                params={"updatePersonFields": update_mask},
                json=person_fields,
            )
            response.raise_for_status()
            return response.json()

        url = f"{PEOPLE_API_BASE}/people:createContact"
        response = requests.post(url, headers=self._headers(), json=person_fields)
        response.raise_for_status()
        return response.json()

    def find_or_upsert_contact(
        self,
        first_name: str,
        last_name: str,
        phone: str = None,
        email: str = None,
        group_name: str = "Candidates",
    ) -> Dict:
        """Search by phone/email first (dedupe against the account's existing
        4000+ contacts), then create or update accordingly."""
        existing = None
        if phone:
            existing = self.search_contact(phone)
        if not existing and email:
            existing = self.search_contact(email)

        group_resource_name = self.get_or_create_group(group_name)
        existing_resource_name = existing["resourceName"] if existing else None

        return self.upsert_contact(
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            email=email,
            existing_resource_name=existing_resource_name,
            group_resource_name=group_resource_name,
        )
