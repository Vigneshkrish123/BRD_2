"""
Authentication gate.

Checks two things before the app is usable:
  1. Azure Key Vault is reachable and returns the API key
  2. Azure OpenAI accepts that key (lightweight ping — 1 token)

Results are cached in Streamlit session state so re-renders don't
trigger repeated auth calls.
"""

from openai import AzureOpenAI, AuthenticationError, APIConnectionError
from loguru import logger

from app.keyvault import get_api_key


class AuthResult:
    def __init__(self, ok: bool, client: AzureOpenAI | None, error: str = ""):
        self.ok     = ok
        self.client = client
        self.error  = error


def verify(endpoint: str, api_version: str, deployment: str) -> AuthResult:
    """
    Run the full auth check. Returns AuthResult with .ok, .client, .error.

    Step 1 — Key Vault
        Fetch the API key via DefaultAzureCredential (az login session).
        Fails fast with a clear message if the vault URL or secret name is wrong,
        or if the user is not logged in via `az login`.

    Step 2 — Azure OpenAI ping
        Creates the AzureOpenAI client and makes a minimal 1-token completion.
        Catches AuthenticationError (wrong key), APIConnectionError (wrong endpoint),
        and any unexpected errors.
    """

    # ── Step 1: Key Vault ─────────────────────────────────────────────────────
    logger.info("Auth | fetching API key from Key Vault...")
    try:
        api_key = get_api_key()
    except EnvironmentError as e:
        return AuthResult(ok=False, client=None, error=f"Key Vault config error: {e}")
    except Exception as e:
        return AuthResult(
            ok=False, client=None,
            error=(
                f"Key Vault unreachable: {e}\n\n"
                "Make sure you are logged in via `az login` and "
                "AZURE_KEYVAULT_URL is correct."
            ),
        )

    # ── Step 2: Azure OpenAI ping ─────────────────────────────────────────────
    logger.info("Auth | pinging Azure OpenAI...")
    try:
        client = AzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version=api_version,
        )

        # Minimal call — 1 token in, 1 token out. Confirms key + endpoint + deployment.
        client.chat.completions.create(
            model=deployment,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )

    except AuthenticationError:
        return AuthResult(
            ok=False, client=None,
            error=(
                "Azure OpenAI rejected the API key. "
                "Check the secret stored in Key Vault matches your deployment's key."
            ),
        )
    except APIConnectionError:
        return AuthResult(
            ok=False, client=None,
            error=(
                f"Cannot reach Azure OpenAI endpoint: {endpoint}\n"
                "Check AZURE_OPENAI_ENDPOINT in your .env file."
            ),
        )
    except Exception as e:
        return AuthResult(ok=False, client=None, error=f"Azure OpenAI error: {e}")

    logger.info("Auth | passed")
    return AuthResult(ok=True, client=client, error="")
