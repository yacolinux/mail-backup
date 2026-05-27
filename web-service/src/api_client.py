"""Cliente HTTP para comunicarse con el backup-service API REST."""

import logging
import os
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class BackupAPIClient:
    """Cliente para el API REST del backup-service."""

    def __init__(self, base_url: str = None, api_key: str = None, timeout: int = 30):
        self.base_url = (base_url or os.environ.get("BACKUP_API_URL", "http://localhost:8001")).rstrip("/")
        self.api_key = api_key or os.environ.get("BACKUP_API_KEY", "")
        self.timeout = timeout

        # Session con retry automático
        self.session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update({
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
        })

    def _get(self, path: str, params: dict = None) -> Dict:
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            if data.get("ok"):
                return data["data"]
            raise ValueError(data.get("error", "Error desconocido"))
        except requests.exceptions.ConnectionError:
            raise ConnectionError(f"No se puede conectar al backup-service en {self.base_url}")
        except requests.exceptions.Timeout:
            raise TimeoutError(f"Timeout conectando a {url}")

    def _post(self, path: str, json_data: dict = None) -> Dict:
        url = f"{self.base_url}{path}"
        resp = self.session.post(url, json=json_data or {}, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok"):
            return data["data"]
        raise ValueError(data.get("error", "Error desconocido"))

    def _delete(self, path: str) -> Dict:
        url = f"{self.base_url}{path}"
        resp = self.session.delete(url, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok"):
            return data["data"]
        raise ValueError(data.get("error", "Error desconocido"))

    # =========================================================================
    # Status
    # =========================================================================

    def get_status(self) -> Dict:
        return self._get("/api/v1/status")

    def health_check(self) -> bool:
        try:
            resp = self.session.get(f"{self.base_url}/api/v1/health", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    # =========================================================================
    # Accounts
    # =========================================================================

    def list_accounts(self, search: str = None, page: int = 1, limit: int = 35,
                      sort_by: str = "email", sort_order: str = "ASC") -> Dict:
        params = {"page": page, "limit": limit}
        if search:
            params["search"] = search
        if sort_by != "email" or sort_order != "ASC":
            params["sort"] = sort_by
            params["order"] = sort_order
        return self._get("/api/v1/accounts", params=params)

    def get_account(self, email: str) -> Dict:
        return self._get(f"/api/v1/accounts/{email}")

    # =========================================================================
    # Emails
    # =========================================================================

    def list_emails(
        self,
        email: str,
        folder: str = None,
        search: str = None,
        date_from: str = None,
        date_to: str = None,
        limit: int = 50,
        offset: int = 0,
        include_deleted: bool = False,
        sort_by: str = "date",
        sort_order: str = "DESC",
    ) -> Dict:
        params = {"limit": limit, "offset": offset}
        if folder:
            params["folder"] = folder
        if search:
            params["search"] = search
        if date_from:
            params["date_from"] = date_from
        if date_to:
            params["date_to"] = date_to
        if include_deleted:
            params["deleted"] = "true"
        if sort_by != "date" or sort_order != "DESC":
            params["sort"] = sort_by
            params["order"] = sort_order
        return self._get(f"/api/v1/accounts/{email}/emails", params=params)

    def get_email(self, email_id: int) -> Dict:
        return self._get(f"/api/v1/emails/{email_id}")

    def get_email_content(self, email_id: int) -> Dict:
        return self._get(f"/api/v1/emails/{email_id}/content")

    def download_email(self, email_id: int, fmt: str = "md") -> bytes:
        """Descarga un email convertido al formato solicitado. Retorna bytes."""
        url = f"{self.base_url}/api/v1/emails/{email_id}/download"
        resp = self.session.get(url, params={"format": fmt}, timeout=60)
        resp.raise_for_status()
        if resp.headers.get("content-type", "").startswith("application/json"):
            data = resp.json()
            raise ValueError(data.get("error", "Error desconocido"))
        return resp.content

    def delete_email(self, email_id: int) -> Dict:
        return self._delete(f"/api/v1/emails/{email_id}")

    def bulk_download_emails(self, email_ids: List[int], fmt: str = "md") -> bytes:
        """Descarga múltiples emails convertidos, empaquetados en ZIP."""
        url = f"{self.base_url}/api/v1/emails/bulk-download"
        resp = self.session.post(url, json={"email_ids": email_ids, "format": fmt}, timeout=120)
        resp.raise_for_status()
        if resp.headers.get("content-type", "").startswith("application/json"):
            data = resp.json()
            raise ValueError(data.get("error", "Error desconocido"))
        return resp.content

    # =========================================================================
    # Snapshots
    # =========================================================================

    def list_snapshots(self, email: str) -> List[Dict]:
        return self._get(f"/api/v1/accounts/{email}/snapshots")

    # =========================================================================
    # Admin
    # =========================================================================

    def trigger_backup(self) -> Dict:
        return self._post("/api/v1/backup/run")

    def apply_retention(self) -> Dict:
        return self._post("/api/v1/retention/apply")

    def get_git_log(self) -> List[Dict]:
        return self._get("/api/v1/git/log")

    def get_config(self) -> Dict:
        return self._get("/api/v1/config")

    def update_config(self, data: dict) -> Dict:
        return self._post("/api/v1/config", json_data=data)

    def test_ssh(self, host: str, user: str, ssh_key: str = "/config/ssh/id_rsa",
                 ssh_port: int = 22) -> Dict:
        return self._post("/api/v1/config/test-ssh", json_data={
            "host": host, "user": user,
            "ssh_key": ssh_key, "ssh_port": ssh_port,
        })

    def factory_reset(self) -> Dict:
        return self._post("/api/v1/reset/factory")

    def example_reset(self) -> Dict:
        return self._post("/api/v1/reset/example")

    def get_logs(self, lines: int = 200) -> Dict:
        return self._get("/api/v1/logs", params={"lines": lines})

    def export_config(self, password: str = None) -> bytes:
        body = {}
        if password:
            body["password"] = password
        url = f"{self.base_url}/api/v1/config/export"
        resp = self.session.post(url, json=body or {}, timeout=30)
        resp.raise_for_status()
        return resp.content

    def import_config(self, file_bytes: bytes, filename: str = "config.json",
                      password: str = None) -> Dict:
        import io
        url = f"{self.base_url}/api/v1/config/import"
        files = {"file": (filename, io.BytesIO(file_bytes),
                          "application/octet-stream" if filename.endswith(".zip") else "application/json")}
        data = {}
        if password:
            data["password"] = password
        resp = self.session.post(url, files=files, data=data, timeout=30)
        resp.raise_for_status()
        d = resp.json()
        if d.get("ok"):
            return d["data"]
        raise ValueError(d.get("error", "Error desconocido"))

    def md_to_pdf(self, markdown_text: str, title: str = "Documento") -> bytes:
        url = f"{self.base_url}/api/v1/utils/md-to-pdf"
        resp = self.session.post(
            url, json={"markdown": markdown_text, "title": title}, timeout=30,
        )
        resp.raise_for_status()
        if resp.headers.get("content-type", "").startswith("application/json"):
            data = resp.json()
            raise ValueError(data.get("error", "Error desconocido"))
        return resp.content
