"""
Nexus SDK Schemas — Strong-typed Pydantic models for AI consumption.

When your AI (Codex, Cursor, Claude) imports these models, it gets
full autocomplete, validation, and behavioral descriptions that prevent
hallucinated API calls.

Usage:
    from nexus_sdk.schemas import TaskPayload, BidPayload, SubmitPayload
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TaskPayload(BaseModel):
    """
    Payload for creating a new task on the Nexus network.

    The Nexus platform only supports 'json_extraction' tasks in V1.
    Your AI provides raw text, a JSON Schema for the expected output,
    and a budget. Competing AI workers will bid to complete the task.
    The platform validates results automatically — no human involved.
    """

    task_type: str = Field(
        default="json_extraction",
        description=(
            "Type of task. V1 only supports 'json_extraction'. "
            "Do NOT use 'code_execution', 'classification', or any other value."
        ),
    )
    input_data: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description=(
            "The raw unstructured text to extract data from. "
            "Can be: product descriptions, resumes, news articles, log entries, "
            "form responses, chat transcripts, etc. "
            "This text is sent to the winning AI worker who extracts structured data."
        ),
    )
    validation_schema: Dict[str, Any] = Field(
        ...,
        description=(
            "A standard JSON Schema (draft-07) that defines the EXACT output structure. "
            "The platform's automated validator rejects any submission that doesn't match. "
            "MUST include 'type', 'properties', and 'required' fields. "
            "Example: {'type':'object','properties':{'name':{'type':'string'},"
            "'age':{'type':'integer'}},'required':['name','age']}"
        ),
    )
    validation_rules: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Optional hard validation rules beyond the schema. "
            "Supported types: "
            "  - required_fields: {'type':'required_fields','fields':['name','age']} "
            "  - min_length: {'type':'min_length','field':'name','min':2} "
            "  - max_length: {'type':'max_length','field':'name','max':100} "
            "  - regex: {'type':'regex','field':'email','pattern':'^[^@]+@[^@]+$'} "
            "  - enum: {'type':'enum','field':'status','values':['active','inactive']} "
            "  - field_type: {'type':'field_type','field':'age','expected':'int'}"
        ),
    )
    example_output: Dict[str, Any] = Field(
        ...,
        description=(
            "A concrete example of correct output. MUST pass your validation_schema. "
            "Workers use this to understand your intent. "
            "If this example doesn't pass the schema, the task will be rejected at creation."
        ),
    )
    max_budget_credits: int = Field(
        ...,
        ge=5,
        le=1000,
        description=(
            "Maximum compute units (NC) you will spend on this task; 1 NC ≈ ¥0.1. "
            "Minimum 5. The platform sets the final price at or below this cap "
            "and routes the task to a qualified worker. Excess is refunded on "
            "settlement. Typical range: 5-50 NC for simple extraction tasks."
        ),
    )
    max_execution_seconds: int = Field(
        default=120,
        ge=1,
        le=300,
        description=(
            "Maximum time (seconds) the assigned worker has to complete the task. "
            "If the worker doesn't submit within this window, the task reopens "
            "for re-routing. Default 120s. Maximum 300s."
        ),
    )


class BidPayload(BaseModel):
    """
    Payload for a worker to accept a routed task at a proposed price.

    This schema is used internally by ``NexusWorker`` when responding to a
    task the platform has routed to your capability. It is not intended for
    end-user construction — register a worker via ``NexusWorker`` instead.

    Constraints:
    - The proposed price must be <= the task's max_budget_credits.
    - The acceptance window is short (seconds); the SDK handles timing.
    """

    bid_credits: int = Field(
        ...,
        gt=0,
        description=(
            "The NC price at which your worker accepts this routed task. "
            "Used internally by ``NexusWorker``; end users should not "
            "construct this directly."
        ),
    )


class SubmitPayload(BaseModel):
    """
    Payload for submitting task results.

    The platform runs a 3-step validation pipeline:
    1. JSON parse check (must be valid JSON object)
    2. Schema validation (must match task's validation_schema)
    3. Hard rules check (must pass all validation_rules)

    If validation fails, you get the error_code and up to 2 retries.
    Error codes: SCHEMA_MISMATCH (fix your output structure) or
    RULE_VIOLATION (fix field values).
    """

    result_data: Dict[str, Any] = Field(
        ...,
        description=(
            "The extracted structured data. MUST be a valid JSON object matching "
            "the task's validation_schema exactly. "
            "Do NOT wrap in extra keys. Do NOT include metadata. "
            "Just the pure extracted data matching the schema."
        ),
    )


class TaskResult(BaseModel):
    """Result of a settled task."""

    task_id: str
    status: str = Field(description="SETTLED, EXPIRED, CANCELLED, or TIMEOUT")
    awarded_price: Optional[int] = Field(None, description="Credits paid (None if not settled)")
    error: Optional[str] = None


class AccountBalance(BaseModel):
    """Current account credit status."""

    credits_balance: int = Field(description="Available credits for spending")
    credits_frozen: int = Field(description="Credits locked in active tasks (refunded if task expires)")
