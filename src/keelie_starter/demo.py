"""A stand-in for Keelie, in memory, for `KEELIE_MODE=demo` and for the tests.

It answers like Keelie on the paths this site uses: the same models, the same problem codes, and
an Idempotency-Key that does the work once. It is not Keelie. It mints no token, takes no payment
and forgets everything when the process stops. A button on the checkout page plays Stripe.
"""

import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from keelie_starter.keelie import KeelieError
from keelie_starter.models import (
    Credential,
    CredentialCreated,
    CreditPack,
    IssuedCredential,
    Payment,
    Plan,
    PlanScope,
    Pricing,
    Subject,
    SubjectSubscription,
    Subscription,
    SubscriptionStatus,
    TokenInfo,
)

DEMO_PRODUCT_ID = "00000000-0000-7000-8000-00000000da11"
DEMO_ORG_ID = "00000000-0000-7000-8000-0000000000de"


def demo_plans() -> list[Plan]:
    """Three plans, one of each shape a Provider sells: free, a monthly fee, and packs only."""

    def plan(n: int, name: str, pricing: Pricing) -> Plan:
        return Plan(
            id=f"00000000-0000-7000-8000-00000000010{n}",
            product_id=DEMO_PRODUCT_ID,
            name=name,
            status="published",
            currency="usd",
            pricing=pricing,
            scopes=[PlanScope(scope="sun:read")],
        )

    return [
        plan(1, "Free", Pricing(fee=0, credits_per_period=1_000)),
        plan(
            2,
            "Pro",
            Pricing(
                fee=900,
                credits_per_period=100_000,
                credit_packs=[CreditPack(credits=50_000, price=500)],
            ),
        ),
        plan(
            3,
            "Pay as you go",
            Pricing(
                fee=0,
                credits_per_period=0,
                credit_packs=[
                    CreditPack(credits=10_000, price=200),
                    CreditPack(credits=100_000, price=1_500),
                ],
            ),
        ),
    ]


@dataclass
class _Sub:
    id: str
    subject_id: str
    plan_id: str
    status: SubscriptionStatus
    balance: Decimal
    scopes: list[str]
    paid_period: str | None = None


@dataclass
class _Cred:
    id: str
    client_id: str
    subscription_id: str
    created_at: str
    revoked_at: str | None = None


@dataclass
class _Pending:
    kind: str
    credits: int


@dataclass
class DemoKeelie:
    """Implements `KeelieApi`. Every id it makes is a fresh UUID."""

    api_url: str = "https://daylight.example.com/v1"
    plans: dict[str, Plan] = field(default_factory=lambda: {p.id: p for p in demo_plans()})
    subjects: dict[str, Subject] = field(default_factory=dict)
    subscriptions: dict[str, _Sub] = field(default_factory=dict)
    credentials: dict[str, _Cred] = field(default_factory=dict)
    # The answer each Idempotency-Key got the first time, so a retry gets it again.
    _subject_replies: dict[str, Subject] = field(default_factory=dict)
    _id_replies: dict[str, str] = field(default_factory=dict)
    _payment_replies: dict[str, Payment] = field(default_factory=dict)
    _pending: dict[str, _Pending] = field(default_factory=dict)

    async def aclose(self) -> None:
        return None

    @staticmethod
    def _id() -> str:
        return str(uuid.uuid4())

    def _sub(self, subscription_id: str) -> _Sub:
        sub = self.subscriptions.get(subscription_id)
        if sub is None:
            raise KeelieError(404, "entity-not-found", "No such subscription.", None)
        return sub

    def _read(self, sub: _Sub) -> Subscription:
        return Subscription(
            id=sub.id,
            subject_id=sub.subject_id,
            plan_id=sub.plan_id,
            status=sub.status,
            credit_balance=f"{sub.balance.normalize():f}",
            scopes=sub.scopes,
            paid_period=sub.paid_period,
        )

    async def create_subject(self, display_name: str, external_ref: str, key: str) -> Subject:
        if (seen := self._subject_replies.get(key)) is not None:
            return seen
        subject = Subject(id=self._id(), display_name=display_name)
        self.subjects[subject.id] = subject
        self._subject_replies[key] = subject
        return subject

    async def get_subject(self, subject_id: str) -> Subject:
        subject = self.subjects.get(subject_id)
        if subject is None:
            raise KeelieError(404, "entity-not-found", "No such subject.", None)
        mine = [
            SubjectSubscription(id=s.id, plan_id=s.plan_id, status=s.status)
            for s in self.subscriptions.values()
            if s.subject_id == subject_id
        ]
        return subject.model_copy(update={"subscriptions": mine})

    async def list_plans(self, product_id: str) -> list[Plan]:
        return [p for p in self.plans.values() if p.product_id == product_id]

    async def get_plan(self, plan_id: str) -> Plan:
        plan = self.plans.get(plan_id)
        if plan is None:
            raise KeelieError(404, "entity-not-found", "No such plan.", None)
        return plan

    async def create_subscription(self, subject_id: str, plan_id: str, key: str) -> Subscription:
        if (seen := self._id_replies.get(key)) is not None:
            return self._read(self._sub(seen))
        plan = await self.get_plan(plan_id)
        if plan.status != "published":
            raise KeelieError(409, "plan-not-published", "That plan is not on sale.", None)
        if any(
            s.subject_id == subject_id and s.plan_id == plan_id for s in self.subscriptions.values()
        ):
            raise KeelieError(409, "entity-conflict", "Already subscribed to that plan.", None)
        status: SubscriptionStatus = "past_due" if plan.pricing.fee > 0 else "active"
        balance = Decimal(0) if plan.pricing.fee > 0 else Decimal(plan.pricing.credits_per_period)
        scopes = [s.scope for s in plan.scopes]
        sub = _Sub(self._id(), subject_id, plan_id, status, balance, scopes)
        self.subscriptions[sub.id] = sub
        self._id_replies[key] = sub.id
        return self._read(sub)

    async def get_subscription(self, subscription_id: str) -> Subscription:
        return self._read(self._sub(subscription_id))

    def _payment(self, plan: Plan, amount: int, credits: int, *, free: bool = False) -> Payment:
        return Payment(
            amount_minor=amount,
            currency=plan.currency,
            credits=credits,
            free=free,
            publishable_key="pk_demo",
            client_secret=None if free else f"demo_secret_{secrets.token_hex(8)}",
            period=datetime.now(UTC).strftime("%Y-%m"),
        )

    async def pay(self, subscription_id: str, consent_ref: str, key: str) -> Payment:
        if (seen := self._payment_replies.get(key)) is not None:
            return seen
        sub = self._sub(subscription_id)
        if sub.status != "past_due":
            raise KeelieError(409, "nothing-to-pay", "Nothing is due.", None)
        plan = await self.get_plan(sub.plan_id)
        credits = plan.pricing.credits_per_period
        payment = self._payment(plan, plan.pricing.fee, credits)
        self._pending[sub.id] = _Pending("pay", credits)
        self._payment_replies[key] = payment
        return payment

    async def top_up(
        self, subscription_id: str, credits: int, save_card: bool, key: str
    ) -> Payment:
        if (seen := self._payment_replies.get(key)) is not None:
            return seen
        sub = self._sub(subscription_id)
        plan = await self.get_plan(sub.plan_id)
        pack = next((p for p in plan.pricing.credit_packs if p.credits == credits), None)
        if pack is None:
            raise KeelieError(422, "validation-error", "That plan has no such pack.", None)
        payment = self._payment(plan, pack.price, credits)
        self._pending[sub.id] = _Pending("top-up", credits)
        self._payment_replies[key] = payment
        return payment

    def confirm_payment(self, subscription_id: str) -> None:
        """What Stripe's webhook does to Keelie, in the real thing. Demo mode only."""
        pending = self._pending.pop(subscription_id, None)
        if pending is None:
            return
        sub = self._sub(subscription_id)
        sub.balance += pending.credits
        if pending.kind == "pay":
            sub.status = "active"
            sub.paid_period = datetime.now(UTC).strftime("%Y-%m")

    async def create_credential(
        self, subscription_id: str, key: str
    ) -> tuple[CredentialCreated, bool]:
        if (seen := self._id_replies.get(key)) is not None:
            # Keelie replays the answer, but never the secret a second time.
            return self._created(self.credentials[seen], None), True
        sub = self._sub(subscription_id)
        cred = _Cred(
            id=self._id(),
            client_id=f"demo-{secrets.token_hex(6)}",
            subscription_id=sub.id,
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        self.credentials[cred.id] = cred
        self._id_replies[key] = cred.id
        secret = f"demo-secret-{secrets.token_urlsafe(24)}"
        return self._created(cred, secret), False

    def _created(self, cred: _Cred, secret: str | None) -> CredentialCreated:
        issued = (
            IssuedCredential(client_id=cred.client_id, client_secret=secret) if secret else None
        )
        return CredentialCreated(**self._credential(cred).model_dump(), credential=issued)

    @staticmethod
    def _credential(cred: _Cred) -> Credential:
        return Credential(
            id=cred.id,
            client_id=cred.client_id,
            subscription_id=cred.subscription_id,
            status="revoked" if cred.revoked_at else "active",
            created_at=cred.created_at,
            revoked_at=cred.revoked_at,
        )

    async def get_credential(self, credential_id: str) -> Credential:
        cred = self.credentials.get(credential_id)
        if cred is None:
            raise KeelieError(404, "entity-not-found", "No such credential.", None)
        return self._credential(cred)

    async def revoke_credential(self, credential_id: str) -> None:
        cred = self.credentials.get(credential_id)
        if cred is None:
            raise KeelieError(404, "entity-not-found", "No such credential.", None)
        if cred.revoked_at:
            raise KeelieError(409, "entity-conflict", "Already revoked.", None)
        cred.revoked_at = datetime.now(UTC).isoformat(timespec="seconds")

    async def token_info(self, org_id: str, product_id: str) -> TokenInfo:
        issuer = f"https://demo.keelie.example/realms/{DEMO_ORG_ID}"
        return TokenInfo(
            issuer=issuer,
            token_endpoint=f"{issuer}/protocol/openid-connect/token",
            resource=self.api_url,
        )
