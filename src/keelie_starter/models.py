"""What the site reads from Keelie. Only the fields it uses; Keelie may send more.

The reference for every field is Keelie's contract, `openapi/keelie.json`. Money is an integer of
minor units. Credits are decimal strings. Neither is ever a float.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

SubscriptionStatus = Literal["pending", "active", "past_due", "suspended", "canceled"]


class KeelieModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class CreditPack(KeelieModel):
    credits: int
    price: int


class Pricing(KeelieModel):
    fee: int
    credits_per_period: int
    period: str = "month"
    credit_packs: list[CreditPack] = []


class PlanScope(KeelieModel):
    scope: str


class Plan(KeelieModel):
    id: str
    product_id: str
    name: str
    status: str
    currency: str
    pricing: Pricing
    scopes: list[PlanScope] = []


class PlanPage(KeelieModel):
    items: list[Plan]
    next_cursor: str | None = None


class Product(KeelieModel):
    id: str
    name: str
    resource: str | None = None


class Org(KeelieModel):
    id: str
    name: str
    issuer: str


class SubjectSubscription(KeelieModel):
    id: str
    plan_id: str
    status: SubscriptionStatus


class Subject(KeelieModel):
    id: str
    display_name: str
    subscriptions: list[SubjectSubscription] = []


class Subscription(KeelieModel):
    id: str
    subject_id: str
    plan_id: str
    status: SubscriptionStatus
    credit_balance: str
    scopes: list[str] = []
    # The last month whose fee is paid, YYYY-MM in UTC. None on a plan with no fee.
    paid_period: str | None = None


class Credential(KeelieModel):
    id: str
    client_id: str
    subscription_id: str
    status: str
    created_at: str
    revoked_at: str | None = None
    # After a revoke, tokens made before it still work until this time.
    tokens_expire_by: str | None = None


class IssuedCredential(KeelieModel):
    client_id: str
    # Shown to the Consumer once, in one response. Never stored, never logged.
    client_secret: str


class CredentialCreated(Credential):
    credential: IssuedCredential | None = None


class Payment(KeelieModel):
    """The answer of `:pay` (SubscriptionPaymentRead) or of `:top-up` (TopUpRead)."""

    amount_minor: int
    currency: str
    credits: int
    publishable_key: str
    free: bool = False
    period: str | None = None
    stripe_account: str | None = None
    # Stripe's client secret for this payment. It goes to the browser for Stripe.js only.
    client_secret: str | None = None


class TokenInfo(KeelieModel):
    """Where a Consumer's credential mints, and the audience its tokens carry."""

    issuer: str
    token_endpoint: str
    resource: str
