import json
import logging
import os
import azure.functions as func

from openai import AsyncAzureOpenAI, OpenAIError
from azure.identity import (
    DefaultAzureCredential,
    InteractiveBrowserCredential,
    get_bearer_token_provider,
)
from azure.core.exceptions import ClientAuthenticationError

app = func.FunctionApp()
log = logging.getLogger(__name__)

COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"
_AOAI_CLIENT: AsyncAzureOpenAI | None = None


def _is_running_in_azure() -> bool:
    return bool(os.getenv("WEBSITE_INSTANCE_ID") or os.getenv("IDENTITY_ENDPOINT") or os.getenv("MSI_ENDPOINT"))


def _get_aoai_settings():
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "")
    tenant_id = os.getenv("AZURE_TENANT_ID", "organizations")
    return endpoint, api_version, deployment, api_key, tenant_id


def _build_credential():
    """
    Azure: DefaultAzureCredential with tenant fallback to support both single-tenant and multi-tenant (e.g. MSA) scenarios.
    Local: InteractiveBrowserCredential to allow developers to sign in with their own accounts (including MSAs which often don't have tenant_id or use "organizations" as tenant_id).
    """
    _, _, _, _, tenant_id = _get_aoai_settings()

    if _is_running_in_azure():
        return DefaultAzureCredential(additionally_allowed_tenants=["*"])

    return InteractiveBrowserCredential(tenant_id=tenant_id)


def _get_aoai_client() -> AsyncAzureOpenAI:
    global _AOAI_CLIENT
    if _AOAI_CLIENT is not None:
        return _AOAI_CLIENT

    endpoint, api_version, _, api_key, _ = _get_aoai_settings()
    if not endpoint:
        raise ValueError("AZURE_OPENAI_ENDPOINT must be configured in Function App settings")

    if api_key:
        _AOAI_CLIENT = AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
            max_retries=0,
        )
        return _AOAI_CLIENT

    credential = _build_credential()
    token_provider = get_bearer_token_provider(credential, COGNITIVE_SCOPE)

    _AOAI_CLIENT = AsyncAzureOpenAI(
        azure_endpoint=endpoint,
        azure_ad_token_provider=token_provider,
        api_version=api_version,
        max_retries=0,
    )
    return _AOAI_CLIENT


def _validate_chat_payload(payload: dict) -> str | None:
    msgs = payload.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return "Missing 'messages'. Expected: {'messages':[{'role':'user','content':'...'}], ...}"
    for i, m in enumerate(msgs):
        if not isinstance(m, dict) or "role" not in m or "content" not in m:
            return f"messages[{i}] must contain 'role' and 'content'"
    return None


@app.function_name(name="getanalyzedresponse")
@app.route(route="process", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
async def get_analyzed_response(req: func.HttpRequest) -> func.HttpResponse:
    """
    proxy endpoint:
      - Client sends AnalyzerAgentClient payload (messages + optional params)
      - Proxy supplies model/deployment internally
    """
    try:
        payload = req.get_json()
    except Exception as e:
        return func.HttpResponse(
            json.dumps({"error": f"Invalid JSON: {str(e)}"}),
            status_code=400,
            mimetype="application/json",
        )

    if not isinstance(payload, dict):
        return func.HttpResponse(
            json.dumps({"error": "Request body must be a JSON object"}),
            status_code=400,
            mimetype="application/json",
        )

    payload.pop("model", None)

    err = _validate_chat_payload(payload)
    if err:
        return func.HttpResponse(json.dumps({"error": err}), status_code=400, mimetype="application/json")

    _, _, deployment, _, _ = _get_aoai_settings()

    try:
        client = _get_aoai_client()
        resp = await client.chat.completions.create(model=deployment, **payload)
        logging.info("AOAI response: %s", resp.model_dump_json(ensure_ascii=True))
        return func.HttpResponse(resp.model_dump_json(), status_code=200, mimetype="application/json")

    except ClientAuthenticationError as e:
        return func.HttpResponse(
            json.dumps({"error": "Authentication failed", "details": str(e)}),
            status_code=401,
            mimetype="application/json",
        )
    except OpenAIError as e:
        status = getattr(e, "status_code", 500)
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=status if isinstance(status, int) else 500,
            mimetype="application/json",
        )
    except Exception as e:
        log.exception("Unhandled error while processing request.")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json")