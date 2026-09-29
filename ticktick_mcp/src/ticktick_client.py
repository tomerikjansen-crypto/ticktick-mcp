import os
import json
import base64
import requests
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from typing import Dict, List, Any, Optional, Tuple

# Set up logging
logger = logging.getLogger(__name__)

# Delt tokenfil med dashboard-serverens Node-tokenmodul (prosjektering-repoet,
# fase 2 K5): ett token-system paa tvers av Python og Node.
TOKEN_FILE = Path.home() / ".ticktick" / "tokens.json"


def normaliser_refresh_token(verdi: Any) -> Optional[str]:
    """Regelen paa ett sted: null, tom streng eller ikke-streng betyr "ingen refresh-token"."""
    if isinstance(verdi, str) and verdi.strip():
        return verdi
    return None


def _parse_tid(verdi: Any) -> Optional[datetime]:
    """Tolk en ISO-tidsstempel (Python +00:00 eller JS Z). None ved feil."""
    if not isinstance(verdi, str) or not verdi:
        return None
    try:
        tid = datetime.fromisoformat(verdi.replace("Z", "+00:00"))
    except ValueError:
        return None
    return tid if tid.tzinfo else tid.replace(tzinfo=timezone.utc)


def skriv_token_fil(access_token: str, refresh_token: Optional[str], kilde: str,
                    oppdatert: Optional[datetime] = None) -> bool:
    """
    Skriv den delte tokenfila atomisk (tmp+replace). Felles for klienten og
    authenticate/auth.py, saa begge lager samme format: access_token,
    refresh_token (streng eller null), oppdatert (ISO) og kilde.

    Midlertidig fil har unikt navn per skriving (mkstemp i samme mappe som
    maalfila), saa samtidige skrivere ikke blander innhold. Egen temp-fil ryddes
    ved feil, og tillatelsene settes til 0o600 der det er stoettet.

    Returnerer True ved vellykket skriving, False ved OSError (logges uten tokenverdier).
    """
    tmp: Optional[Path] = None
    try:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "access_token": access_token,
            "refresh_token": normaliser_refresh_token(refresh_token),
            "oppdatert": (oppdatert or datetime.now(timezone.utc)).isoformat(),
            "kilde": kilde,
        }
        fd, tmp_navn = tempfile.mkstemp(dir=str(TOKEN_FILE.parent),
                                        prefix=TOKEN_FILE.name + ".", suffix=".tmp")
        tmp = Path(tmp_navn)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, indent=2))
        # Lukket foer replace (viktig paa Windows). Ingen feil hvis chmod ikke stoettes.
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(TOKEN_FILE)
        tmp = None
        return True
    except OSError as e:
        logger.warning(f"Could not write shared token file: {e}")
        return False
    finally:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass


class TickTickClient:
    """
    Client for the TickTick API using OAuth2 authentication.
    """

    # Tidsstempel for siste tokenfil klienten leste eller skrev (adopsjon etter 401)
    _fil_oppdatert: Optional[datetime] = None

    def __init__(self):
        load_dotenv()
        self.client_id = os.getenv("TICKTICK_CLIENT_ID")
        self.client_secret = os.getenv("TICKTICK_CLIENT_SECRET")
        self.access_token = os.getenv("TICKTICK_ACCESS_TOKEN")
        self.refresh_token = os.getenv("TICKTICK_REFRESH_TOKEN")

        # Delt tokenfil (skrives av dashboard-serverens token-modul) har
        # forrang over .env: ett token-system paa tvers av Node og Python (K5)
        shared = self._read_token_file()
        if shared:
            self.access_token = shared.get("access_token") or self.access_token
            # Fila er autoritativ for refresh_token, ogsaa naar verdien er
            # eksplisitt null (ingen refresh-token utstedt, ny innlogging
            # kreves). Da skal en gammel verdi fra miljoe/.env IKKE brukes.
            self.refresh_token = self._refresh_fra_fil(shared, self.refresh_token)
            self._fil_oppdatert = _parse_tid(shared.get("oppdatert"))

        if not self.access_token:
            raise ValueError("TICKTICK_ACCESS_TOKEN environment variable is not set. "
                            "Please run 'uv run -m ticktick_mcp.authenticate' to set up your credentials.")
            
        self.base_url = os.getenv("TICKTICK_BASE_URL") or "https://api.ticktick.com/open/v1"
        self.token_url = os.getenv("TICKTICK_TOKEN_URL") or "https://ticktick.com/oauth/token"
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept-Encoding": None,
            "User-Agent": 'curl/8.7.1'
        }
    
    def _refresh_access_token(self) -> bool:
        """
        Refresh the access token using the refresh token.
        
        Returns:
            True if successful, False otherwise
        """
        if not self.refresh_token:
            logger.warning("No refresh token available. Cannot refresh access token.")
            return False
            
        if not self.client_id or not self.client_secret:
            logger.warning("Client ID or Client Secret missing. Cannot refresh access token.")
            return False
            
        # Prepare the token request
        token_data = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token
        }
        
        # Prepare Basic Auth credentials
        auth_str = f"{self.client_id}:{self.client_secret}"
        auth_bytes = auth_str.encode('ascii')
        auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
        
        headers = {
            "Authorization": f"Basic {auth_b64}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        try:
            # Send the token request
            response = requests.post(self.token_url, data=token_data, headers=headers)
            response.raise_for_status()
            
            # Parse the response
            tokens = response.json()

            # Et svar uten access_token er ubrukelig: behold gamle verdier
            # (logger aldri tokenverdier)
            ny_access = tokens.get('access_token') if isinstance(tokens, dict) else None
            if not ny_access:
                logger.error("Refresh response had no access_token. Keeping existing tokens.")
                return False

            # Update the tokens
            self.access_token = ny_access
            # Et OAuth-svar er IKKE tokenfila: manglende, null eller tom
            # refresh_token betyr at serveren ikke roterte. Behold da den
            # gamle (samme som dashbordet, ticktick-token.mjs).
            ny_refresh = normaliser_refresh_token(tokens.get('refresh_token'))
            if ny_refresh:
                self.refresh_token = ny_refresh

            # Update the headers
            self.headers["Authorization"] = f"Bearer {self.access_token}"
            
            # Den autoritative tokenfila FOERST (den registrerer ogsaa klientens
            # nye tidspunkt foer noe kan feile), slik at Node-serveren ser fornyelsen
            self._write_token_file()

            # .env er sekundaer lagring: en feil her skal aldri hindre resten
            try:
                self._save_tokens_to_env(tokens)
            except OSError as e:
                logger.warning(f"Could not write .env file ({type(e).__name__}). "
                               "The shared token file was handled separately.")

            logger.info("Access token refreshed successfully.")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error refreshing access token: {e}")
            return False
    
    def _save_tokens_to_env(self, tokens: Dict[str, str]) -> None:
        """
        Save the tokens to the .env file.
        
        Args:
            tokens: A dictionary containing the access_token and optionally refresh_token
        """
        # Load existing .env file content
        env_path = Path('.env')
        env_content = {}
        
        if env_path.exists():
            with open(env_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        env_content[key] = value
        
        # Update with new tokens
        env_content["TICKTICK_ACCESS_TOKEN"] = tokens.get('access_token', '')
        # Bare en faktisk rotert refresh-token skrives: null/tom lar den
        # eksisterende stå, og "None" kan aldri havne i .env
        ny_refresh = normaliser_refresh_token(tokens.get('refresh_token'))
        if ny_refresh:
            env_content["TICKTICK_REFRESH_TOKEN"] = ny_refresh
        
        # Make sure client credentials are saved as well
        if self.client_id and "TICKTICK_CLIENT_ID" not in env_content:
            env_content["TICKTICK_CLIENT_ID"] = self.client_id
        if self.client_secret and "TICKTICK_CLIENT_SECRET" not in env_content:
            env_content["TICKTICK_CLIENT_SECRET"] = self.client_secret
        
        # Write back to .env file
        with open(env_path, 'w') as f:
            for key, value in env_content.items():
                f.write(f"{key}={value}\n")
        
        logger.debug("Tokens saved to .env file")

    @staticmethod
    def _refresh_fra_fil(shared: Dict, fallback: Optional[str]) -> Optional[str]:
        """
        Velg refresh_token naar den delte TOKENFILA er lest (har access_token).

        Har fila noekkelen `refresh_token`, er den autoritativ: en streng
        brukes, null (eller tom streng) gir None uten fallback (ticktick-auth.mjs
        skriver null med vilje naar TickTick ikke ga refresh-token). Mangler
        noekkelen, brukes fallback (miljoe/.env/minne) som for.

        Dette gjelder KUN tokenfila. Et OAuth-svar paa en fornyelse er noe
        annet: der betyr manglende/null/tom refresh_token "ikke rotert", og den
        gamle beholdes (se _refresh_access_token, samme som dashbordet).

        Kjent forskjell mot dashbordet (ticktick-token.mjs), bevisst ikke rettet
        her: dashbordet har ingen fallback ved manglende noekkel (overtar hele
        filobjektet). Harmonisering er egen endring.
        """
        if "refresh_token" in shared:
            return normaliser_refresh_token(shared["refresh_token"])
        return fallback

    def _read_token_file(self) -> Optional[Dict]:
        """Les den delte tokenfila. Returnerer None ved manglende/korrupt fil."""
        try:
            if TOKEN_FILE.exists():
                data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    logger.warning("Shared token file is not a JSON object. Ignoring it.")
                    return None
                token = data.get("access_token")
                if isinstance(token, str) and token.strip():
                    return data
        except (OSError, ValueError) as e:
            # ValueError dekker baade JSONDecodeError og UnicodeDecodeError
            logger.warning(f"Could not read shared token file: {e}")
        return None

    def _write_token_file(self) -> None:
        """Skriv gjeldende tokens til den delte fila (atomisk via tmp+replace)."""
        tid = datetime.now(timezone.utc)
        # Sett tidsstempelet ogsaa ved mislykket skriving: minnet er da nyere
        # enn fila, og en eldre fil skal ikke adopteres etter en senere 401
        self._fil_oppdatert = tid
        skriv_token_fil(self.access_token, self.refresh_token, "python-refresh", tid)

    def _filen_er_nyere(self, shared: Dict) -> bool:
        """
        Er den delte fila nyere enn det klienten sist leste eller skrev?
        Har klienten et kjent tidspunkt, kreves et STRENGT nyere, gyldig
        tidspunkt i fila: like, manglende og ugyldige tidsstempel avvises, uansett
        om access_token er ulik. Bare uten kjent tidspunkt (fersk start uten lest
        fil) brukes ulik access_token som bevis, som dashbordet (ticktick-token.mjs).
        """
        if self._fil_oppdatert:
            fil_tid = _parse_tid(shared.get("oppdatert"))
            return bool(fil_tid and fil_tid > self._fil_oppdatert)
        return shared["access_token"] != self.access_token

    def _send(self, method: str, url: str, data=None):
        """Send ett HTTP-kall med gjeldende headers."""
        if method == "GET":
            return requests.get(url, headers=self.headers)
        elif method == "POST":
            return requests.post(url, headers=self.headers, json=data)
        elif method == "DELETE":
            return requests.delete(url, headers=self.headers)
        raise ValueError(f"Unsupported HTTP method: {method}")

    def _make_request(self, method: str, endpoint: str, data=None) -> Dict:
        """
        Makes a request to the TickTick API.
        
        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint (without base URL)
            data: Request data (for POST, PUT)
        
        Returns:
            API response as a dictionary
        """
        url = f"{self.base_url}{endpoint}"
        
        try:
            response = self._send(method, url, data)

            if response.status_code == 401:
                # Node-serveren kan alt ha fornyet tokenet - les delt fil
                # foer vi brenner vaar egen refresh (refresh_token roterer)
                shared = self._read_token_file()
                if shared and self._filen_er_nyere(shared):
                    self._fil_oppdatert = _parse_tid(shared.get("oppdatert")) or self._fil_oppdatert
                    if shared["access_token"] != self.access_token:
                        # Ny access_token adopteres: fila er autoritativ for
                        # refresh_token ogsaa naar den er null
                        logger.info("Using refreshed token from shared token file.")
                        self.refresh_token = self._refresh_fra_fil(shared, self.refresh_token)
                        self.access_token = shared["access_token"]
                        self.headers["Authorization"] = f"Bearer {self.access_token}"
                        response = self._send(method, url, data)
                    else:
                        # Samme access_token: null/manglende i fila skal aldri nulle et
                        # gyldig refresh-token i minnet, bare en faktisk ny streng adopteres
                        self.refresh_token = normaliser_refresh_token(
                            shared.get("refresh_token")) or self.refresh_token

            if response.status_code == 401:
                logger.info("Access token expired. Attempting to refresh...")
                if self._refresh_access_token():
                    response = self._send(method, url, data)

            # Raise an exception for 4xx/5xx status codes
            response.raise_for_status()

            # Return empty dict for 204 No Content
            if response.status_code == 204 or response.text == "":
                return {}

            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {e}")
            return {"error": str(e)}
    
    # Project methods
    def get_projects(self) -> List[Dict]:
        """Gets all projects for the user."""
        return self._make_request("GET", "/project")
    
    def get_project(self, project_id: str) -> Dict:
        """Gets a specific project by ID."""
        return self._make_request("GET", f"/project/{project_id}")
    
    def get_project_with_data(self, project_id: str) -> Dict:
        """Gets project with tasks and columns."""
        return self._make_request("GET", f"/project/{project_id}/data")
    
    def create_project(self, name: str, color: str = "#F18181", view_mode: str = "list", kind: str = "TASK") -> Dict:
        """Creates a new project."""
        data = {
            "name": name,
            "color": color,
            "viewMode": view_mode,
            "kind": kind
        }
        return self._make_request("POST", "/project", data)
    
    def update_project(self, project_id: str, name: str = None, color: str = None, 
                       view_mode: str = None, kind: str = None) -> Dict:
        """Updates an existing project."""
        data = {}
        if name:
            data["name"] = name
        if color:
            data["color"] = color
        if view_mode:
            data["viewMode"] = view_mode
        if kind:
            data["kind"] = kind
            
        return self._make_request("POST", f"/project/{project_id}", data)
    
    def delete_project(self, project_id: str) -> Dict:
        """Deletes a project."""
        return self._make_request("DELETE", f"/project/{project_id}")
    
    # Task methods
    def get_task(self, project_id: str, task_id: str) -> Dict:
        """Gets a specific task by project ID and task ID."""
        return self._make_request("GET", f"/project/{project_id}/task/{task_id}")
    
    def create_task(self, title: str, project_id: str, content: str = None, 
                   start_date: str = None, due_date: str = None, 
                   priority: int = 0, is_all_day: bool = False) -> Dict:
        """Creates a new task."""
        data = {
            "title": title,
            "projectId": project_id
        }
        
        if content:
            data["content"] = content
        if start_date:
            data["startDate"] = start_date
        if due_date:
            data["dueDate"] = due_date
        if priority is not None:
            data["priority"] = priority
        if is_all_day is not None:
            data["isAllDay"] = is_all_day
            
        return self._make_request("POST", "/task", data)
    
    def update_task(self, task_id: str, project_id: str, title: str = None, 
                   content: str = None, priority: int = None, 
                   start_date: str = None, due_date: str = None) -> Dict:
        """Updates an existing task."""
        data = {
            "id": task_id,
            "projectId": project_id
        }
        
        if title:
            data["title"] = title
        if content:
            data["content"] = content
        if priority is not None:
            data["priority"] = priority
        if start_date:
            data["startDate"] = start_date
        if due_date:
            data["dueDate"] = due_date
            
        return self._make_request("POST", f"/task/{task_id}", data)
    
    def complete_task(self, project_id: str, task_id: str) -> Dict:
        """Marks a task as complete."""
        return self._make_request("POST", f"/project/{project_id}/task/{task_id}/complete")
    
    def delete_task(self, project_id: str, task_id: str) -> Dict:
        """Deletes a task."""
        return self._make_request("DELETE", f"/project/{project_id}/task/{task_id}")
    
    def create_subtask(self, subtask_title: str, parent_task_id: str, project_id: str, 
                      content: str = None, priority: int = 0) -> Dict:
        """
        Creates a subtask for a parent task within the same project.
        
        Args:
            subtask_title: Title of the subtask
            parent_task_id: ID of the parent task
            project_id: ID of the project (must be same for both parent and subtask)
            content: Optional content/description for the subtask
            priority: Priority level (0-3, where 3 is highest)
        
        Returns:
            API response as a dictionary containing the created subtask
        """
        data = {
            "title": subtask_title,
            "projectId": project_id,
            "parentId": parent_task_id
        }
        
        if content:
            data["content"] = content
        if priority is not None:
            data["priority"] = priority
            
        return self._make_request("POST", "/task", data)