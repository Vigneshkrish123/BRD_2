import os
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from loguru import logger


def get_api_key() -> str:
    """
    Retrieve the Azure OpenAI API key from Azure Key Vault.

    Requires these env vars:
        AZURE_KEYVAULT_URL        e.g. https://your-vault.vault.azure.net/
        AZURE_API_KEY_SECRET_NAME e.g. openai-api-key

    Auth is handled by DefaultAzureCredential — on your local machine
    this uses your active `az login` session. No service principal needed.
    """
    vault_url = os.environ.get("AZURE_KEYVAULT_URL")
    secret_name = os.environ.get("AZURE_API_KEY_SECRET_NAME")

    if not vault_url:
        raise EnvironmentError("AZURE_KEYVAULT_URL is not set in environment.")
    if not secret_name:
        raise EnvironmentError("AZURE_API_KEY_SECRET_NAME is not set in environment.")

    logger.info(f"Fetching secret '{secret_name}' from Key Vault...")
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=vault_url, credential=credential)
    secret = client.get_secret(secret_name)
    logger.info("API key retrieved successfully.")
    return secret.value
