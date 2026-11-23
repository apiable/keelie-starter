"""The Consumer website of an API that sells through Keelie.

What Keelie does not do, this website does: its own login, the plan page, the payment page with
Stripe.js, and the credential page. Keelie supplies the API behind each page. The shape follows
Keelie's consumer-site guide, prompts 00 to 05:

- Every call to Keelie goes from this server. The browser never calls Keelie, and never sees the
  Provider's secret or a Keelie token.
- Every id the browser sends is checked against the logged-in user before Keelie is called.
- Every write to Keelie carries an Idempotency-Key made from the action, so a double click or a
  retry does the work once.
- A payment is done when Keelie says so, after Stripe's webhook. The site never marks it paid.
"""

import logging
import secrets
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from keelie_starter import display
from keelie_starter.config import Settings
from keelie_starter.db import Database, Session, SubscriptionLink, User
from keelie_starter.demo import DEMO_ORG_ID, DEMO_PRODUCT_ID, DemoKeelie
from keelie_starter.keelie import KeelieApi, KeelieClient, KeelieError, action_key
from keelie_starter.models import Payment, Plan, Subscription

HERE = Path(__file__).parent
COOKIE = "site_session"
REPO_URL = "https://github.com/apiable/keelie-starter"

log = logging.getLogger("keelie_starter")


class LoginRequired(Exception):
    pass


class NotYours(Exception):
    """An id from the browser that is not the user's. Answered as 404, never as 403."""


def make_keelie(settings: Settings) -> KeelieApi:
    if settings.demo:
        return DemoKeelie(api_url=settings.demo_api_url)
    return KeelieClient(
        settings.keelie_api,
        settings.keelie_token_url,
        settings.keelie_client_id,
        settings.keelie_client_secret.get_secret_value(),
    )


def create_app(
    settings: Settings | None = None,
    keelie: KeelieApi | None = None,
    db: Database | None = None,
) -> FastAPI:
    settings = settings or Settings()
    keelie = keelie or make_keelie(settings)
    db = db or Database(settings.database_path)
    hasher = PasswordHasher()
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.globals.update(
        money=display.money,
        credits_text=display.credits_text,
        per_credit=display.per_credit,
        settings=settings,
        repo_url=REPO_URL,
    )
    org_id = DEMO_ORG_ID if settings.demo else settings.keelie_org_id
    product_id = DEMO_PRODUCT_ID if settings.demo else settings.keelie_product_id
    site_url = settings.site_url.rstrip("/")
    # Every Idempotency-Key is a UUIDv5 in this namespace, made from the user and the action.
    key_namespace = uuid.uuid5(uuid.NAMESPACE_URL, f"{site_url}/keelie-idempotency")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info("%s starts in %s mode", settings.site_name, settings.keelie_mode)
        yield
        await keelie.aclose()

    app = FastAPI(
        title=settings.site_name,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.keelie = keelie
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    # Sessions, security headers and the log line.

    @app.middleware("http")
    async def session_and_log(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        token = request.cookies.get(COOKIE)
        request.state.session = db.session(token) or db.new_session(None)
        response = await call_next(request)
        current: Session = request.state.session
        if current.token != token:
            response.set_cookie(
                COOKIE,
                current.token,
                httponly=True,
                samesite="lax",
                secure=settings.secure_cookies,
                max_age=8 * 3600,
            )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        # The path only. Stripe adds the payment's client secret to the return URL's query string.
        log.info("%s %s %s", request.method, request.url.path, response.status_code)
        return response

    def page(
        request: Request, name: str, status_code: int = 200, **context: object
    ) -> HTMLResponse:
        session: Session = request.state.session
        user = db.user(session.user_id) if session.user_id else None
        return templates.TemplateResponse(
            request, name, {"user": user, "csrf": session.csrf, **context}, status_code=status_code
        )

    @app.exception_handler(LoginRequired)
    async def to_login(request: Request, exc: LoginRequired) -> Response:
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(NotYours)
    async def not_yours(request: Request, exc: NotYours) -> Response:
        return page(
            request, "error.html", 404, title="Not found", detail="We couldn't find that page."
        )

    @app.exception_handler(KeelieError)
    async def keelie_error(request: Request, exc: KeelieError) -> Response:
        log.warning("keelie error %s %s request_id=%s", exc.status, exc.code, exc.request_id)
        status = exc.status if 400 <= exc.status < 500 else 502
        return page(
            request,
            "error.html",
            status,
            title="That didn't work",
            detail=exc.detail,
            request_id=exc.request_id,
        )

    def current_user(request: Request) -> User:
        session: Session = request.state.session
        user = db.user(session.user_id) if session.user_id else None
        if user is None:
            raise LoginRequired
        return user

    def check_csrf(request: Request, token: str) -> None:
        if not secrets.compare_digest(token, request.state.session.csrf):
            raise NotYours

    def owned(user: User, subscription_id: str) -> SubscriptionLink:
        """Every id from the browser is checked against the logged-in user before Keelie is called."""
        link = db.owned_subscription(user.id, subscription_id)
        if link is None:
            raise NotYours
        return link

    def payments_paused(request: Request, error: KeelieError) -> Response:
        log.error(
            "the Provider's Stripe account can't take payments: request_id=%s", error.request_id
        )
        return page(
            request,
            "error.html",
            503,
            title="Payments are paused",
            detail="We can't take payments right now. We've been told, and we're on it.",
            request_id=error.request_id,
        )

    async def ensure_subject(user: User) -> str | None:
        """Make the user's Keelie subject once. A failure is retried on the next page, same key."""
        if user.subject_id:
            return user.subject_id
        try:
            subject = await keelie.create_subject(user.display_name, user.id, user.subject_key)
        except KeelieError as error:
            log.warning(
                "subject not made yet for user %s: %s request_id=%s",
                user.id,
                error.code,
                error.request_id,
            )
            return None
        db.set_subject(user.id, subject.id)
        log.info("subject %s made for user %s", subject.id, user.id)
        return subject.id

    async def published_plans() -> list[Plan]:
        return [p for p in await keelie.list_plans(product_id) if p.status == "published"]

    def next_page_for(subscription: Subscription, plan: Plan) -> str:
        sid = subscription.id
        pricing = plan.pricing
        if subscription.status == "past_due":
            return f"/subscriptions/{sid}/pay"
        if (
            subscription.status == "active"
            and pricing.credits_per_period == 0
            and pricing.credit_packs
        ):
            return f"/subscriptions/{sid}/top-up"
        return f"/subscriptions/{sid}"

    # Home, signup and login.

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> Response:
        session: Session = request.state.session
        user = db.user(session.user_id) if session.user_id else None
        if user is None:
            return page(request, "home.html", plans=await published_plans())
        subject_id = await ensure_subject(user)
        rows = []
        for link in db.subscriptions(user.id):
            subscription = await keelie.get_subscription(link.subscription_id)
            plan = await keelie.get_plan(link.plan_id)
            rows.append({"subscription": subscription, "plan": plan})
        return page(request, "dashboard.html", subject_id=subject_id, rows=rows)

    @app.get("/terms", response_class=HTMLResponse)
    async def terms(request: Request) -> Response:
        return page(request, "terms.html")

    @app.get("/signup", response_class=HTMLResponse)
    async def signup_form(request: Request) -> Response:
        return page(request, "signup.html")

    @app.post("/signup")
    async def signup(
        request: Request,
        csrf: str = Form(),
        email: str = Form(),
        password: str = Form(),
        display_name: str = Form(),
    ) -> Response:
        check_csrf(request, csrf)
        email = email.strip().lower()
        display_name = display_name.strip()
        again = {"email": email, "display_name": display_name}
        if "@" not in email or len(password) < 12 or not display_name:
            error = "Use a real email, a name, and a password of 12 characters or more."
            return page(request, "signup.html", 422, error=error, **again)
        if db.user_by_email(email):
            error = "That email has an account. Log in instead."
            return page(request, "signup.html", 409, error=error, **again)
        user = db.create_user(email, hasher.hash(password), display_name, key_namespace)
        request.state.session = db.new_session(user.id, old_token=request.state.session.token)
        await ensure_subject(user)
        return RedirectResponse("/plans", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request) -> Response:
        return page(request, "login.html")

    @app.post("/login")
    async def login(
        request: Request, csrf: str = Form(), email: str = Form(), password: str = Form()
    ) -> Response:
        check_csrf(request, csrf)
        user = db.user_by_email(email.strip().lower())
        try:
            if user is None:
                # Spend the same time as a real check, so the answer doesn't say who has an account.
                hasher.hash(password)
                raise VerificationError
            hasher.verify(user.password_hash, password)
        except (VerificationError, InvalidHashError):
            error = "That email and password don't match."
            return page(request, "login.html", 401, error=error, email=email)
        request.state.session = db.new_session(user.id, old_token=request.state.session.token)
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    async def logout(request: Request, csrf: str = Form()) -> Response:
        check_csrf(request, csrf)
        request.state.session = db.new_session(None, old_token=request.state.session.token)
        return RedirectResponse("/", status_code=303)

    # Plans and the subscription (prompt 03).

    @app.get("/plans", response_class=HTMLResponse)
    async def plans_page(request: Request) -> Response:
        user = current_user(request)
        subject_id = await ensure_subject(user)
        mine = {link.plan_id: link.subscription_id for link in db.subscriptions(user.id)}
        return page(
            request, "plans.html", plans=await published_plans(), mine=mine, subject_id=subject_id
        )

    @app.post("/subscribe")
    async def subscribe(request: Request, csrf: str = Form(), plan_id: str = Form()) -> Response:
        check_csrf(request, csrf)
        user = current_user(request)
        plan = next((p for p in await published_plans() if p.id == plan_id), None)
        if plan is None:
            raise NotYours
        mine = {link.plan_id: link.subscription_id for link in db.subscriptions(user.id)}
        if plan_id in mine:
            return RedirectResponse(f"/subscriptions/{mine[plan_id]}", status_code=303)
        subject_id = await ensure_subject(user)
        if subject_id is None:
            detail = "We couldn't set up your account. Please try again in a minute."
            return page(request, "error.html", 503, title="Not ready yet", detail=detail)
        key = action_key(key_namespace, "subscribe", user.id, plan_id)
        try:
            subscription = await keelie.create_subscription(subject_id, plan_id, key)
        except KeelieError as error:
            if error.code == "entity-conflict":
                # Most often already on this plan. The type alone can't say so: read the subject.
                subject = await keelie.get_subject(subject_id)
                found = next((s for s in subject.subscriptions if s.plan_id == plan_id), None)
                if found is None:
                    raise
                db.add_subscription(user.id, plan_id, found.id)
                return RedirectResponse(f"/subscriptions/{found.id}", status_code=303)
            if error.code == "plan-not-published":
                log.warning("plan %s is not published, hidden", plan_id)
                return page(
                    request,
                    "error.html",
                    409,
                    title="That plan isn't available",
                    detail=error.detail,
                    request_id=error.request_id,
                )
            raise
        db.add_subscription(user.id, plan_id, subscription.id)
        log.info("subscription %s (%s) for user %s", subscription.id, subscription.status, user.id)
        return RedirectResponse(next_page_for(subscription, plan), status_code=303)

    @app.get("/subscriptions/{subscription_id}", response_class=HTMLResponse)
    async def subscription_page(request: Request, subscription_id: str) -> Response:
        user = current_user(request)
        link = owned(user, subscription_id)
        subscription = await keelie.get_subscription(subscription_id)
        plan = await keelie.get_plan(link.plan_id)
        return page(request, "subscription.html", subscription=subscription, plan=plan)

    # Payment: the monthly fee and packs (prompt 04).

    @app.get("/subscriptions/{subscription_id}/pay", response_class=HTMLResponse)
    async def pay_form(request: Request, subscription_id: str) -> Response:
        user = current_user(request)
        link = owned(user, subscription_id)
        subscription = await keelie.get_subscription(subscription_id)
        if subscription.status != "past_due":
            return RedirectResponse(f"/subscriptions/{subscription_id}", status_code=303)
        plan = await keelie.get_plan(link.plan_id)
        return page(request, "pay.html", subscription=subscription, plan=plan)

    @app.post("/subscriptions/{subscription_id}/pay")
    async def pay(
        request: Request, subscription_id: str, csrf: str = Form(), agree: str = Form("")
    ) -> Response:
        check_csrf(request, csrf)
        user = current_user(request)
        link = owned(user, subscription_id)
        plan = await keelie.get_plan(link.plan_id)
        if agree != settings.terms_version:
            subscription = await keelie.get_subscription(subscription_id)
            error = "Tick the box to agree to the terms."
            return page(request, "pay.html", 422, subscription=subscription, plan=plan, error=error)
        db.add_consent(subscription_id, settings.terms_version)
        month = datetime.now(UTC).strftime("%Y-%m")
        # One key per subscription, month and terms: a double click or a retry pays once.
        key = action_key(key_namespace, "pay", subscription_id, month, settings.terms_version)
        try:
            payment = await keelie.pay(subscription_id, settings.terms_version, key)
        except KeelieError as error:
            if error.code == "nothing-to-pay":
                return RedirectResponse(f"/subscriptions/{subscription_id}", status_code=303)
            if error.code == "payments-not-ready":
                return payments_paused(request, error)
            raise
        log.info(
            "payment started for %s: period %s, free=%s",
            subscription_id,
            payment.period,
            payment.free,
        )
        if payment.free:
            return RedirectResponse(
                f"/subscriptions/{subscription_id}/return?kind=pay", status_code=303
            )
        return checkout(request, subscription_id, payment, kind="pay", n=None, plan=plan)

    @app.get("/subscriptions/{subscription_id}/top-up", response_class=HTMLResponse)
    async def top_up_form(request: Request, subscription_id: str) -> Response:
        user = current_user(request)
        link = owned(user, subscription_id)
        subscription = await keelie.get_subscription(subscription_id)
        plan = await keelie.get_plan(link.plan_id)
        n = db.top_up_count(subscription_id)
        return page(request, "top_up.html", subscription=subscription, plan=plan, n=n)

    @app.post("/subscriptions/{subscription_id}/top-up")
    async def top_up(
        request: Request,
        subscription_id: str,
        csrf: str = Form(),
        credits: int = Form(),
        n: int = Form(),
        save_card: str = Form(""),
    ) -> Response:
        check_csrf(request, csrf)
        user = current_user(request)
        link = owned(user, subscription_id)
        plan = await keelie.get_plan(link.plan_id)
        if credits not in {pack.credits for pack in plan.pricing.credit_packs}:
            raise NotYours
        if n < 0 or n > db.top_up_count(subscription_id):
            raise NotYours
        if db.top_up(subscription_id, n) is None:
            subscription = await keelie.get_subscription(subscription_id)
            db.start_top_up(subscription_id, n, credits, subscription.credit_balance)
        # The nth purchase of this pack: a double click sends the same n, so the same key.
        key = action_key(key_namespace, "top-up", subscription_id, str(n), str(credits))
        try:
            payment = await keelie.top_up(subscription_id, credits, save_card == "yes", key)
        except KeelieError as error:
            if error.code == "payments-not-ready":
                return payments_paused(request, error)
            raise
        log.info("top-up %s started for %s: %s credits", n, subscription_id, credits)
        return checkout(request, subscription_id, payment, kind="top-up", n=n, plan=plan)

    def checkout(
        request: Request,
        subscription_id: str,
        payment: Payment,
        kind: str,
        n: int | None,
        plan: Plan,
    ) -> Response:
        """The Stripe.js page. The browser gets the publishable key, the account and the client
        secret of this one payment. Nothing else."""
        query = {"kind": kind} if n is None else {"kind": kind, "n": str(n)}
        response = page(
            request,
            "checkout.html",
            plan=plan,
            kind=kind,
            subscription_id=subscription_id,
            stripe={
                "publishable_key": payment.publishable_key,
                "stripe_account": payment.stripe_account,
                "client_secret": payment.client_secret,
            },
            amount=display.money(payment.amount_minor, payment.currency),
            credits=payment.credits,
            return_url=f"{site_url}/subscriptions/{subscription_id}/return?{urlencode(query)}",
            return_path=f"/subscriptions/{subscription_id}/return?{urlencode(query)}",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/subscriptions/{subscription_id}/return", response_class=HTMLResponse)
    async def payment_return(
        request: Request, subscription_id: str, kind: str = "pay", n: int | None = None
    ) -> Response:
        user = current_user(request)
        owned(user, subscription_id)
        query = {"kind": kind} if n is None else {"kind": kind, "n": str(n)}
        if "payment_intent_client_secret" in request.query_params:
            # Stripe puts the client secret in the return URL. Drop it from the address bar at once.
            return RedirectResponse(
                f"/subscriptions/{subscription_id}/return?{urlencode(query)}", status_code=303
            )
        state_url = f"/subscriptions/{subscription_id}/state?{urlencode(query)}"
        return page(request, "return.html", subscription_id=subscription_id, state_url=state_url)

    @app.get("/subscriptions/{subscription_id}/state")
    async def payment_state(
        request: Request, subscription_id: str, kind: str = "pay", n: int | None = None
    ) -> Response:
        """What the return page polls: our server reads Keelie, the browser reads us."""
        user = current_user(request)
        owned(user, subscription_id)
        subscription = await keelie.get_subscription(subscription_id)
        done = subscription.status == "active"
        if kind == "top-up" and n is not None:
            started = db.top_up(subscription_id, n)
            done = started is not None and Decimal(subscription.credit_balance) > Decimal(
                started.balance_before
            )
        return JSONResponse(
            {
                "done": done,
                "status": subscription.status,
                "balance": display.credits_text(subscription.credit_balance),
            }
        )

    if settings.demo:

        @app.post("/demo/subscriptions/{subscription_id}/confirm-payment")
        async def demo_confirm_payment(
            request: Request, subscription_id: str, csrf: str = Form(), return_path: str = Form()
        ) -> Response:
            """Demo mode only: plays Stripe and its webhook, then goes where Stripe would."""
            check_csrf(request, csrf)
            user = current_user(request)
            owned(user, subscription_id)
            if not return_path.startswith(f"/subscriptions/{subscription_id}/return?"):
                raise NotYours
            if isinstance(keelie, DemoKeelie):
                keelie.confirm_payment(subscription_id)
            return RedirectResponse(return_path, status_code=303)

    # The credential page (prompt 05).

    @app.get("/subscriptions/{subscription_id}/credentials", response_class=HTMLResponse)
    async def credentials_page(request: Request, subscription_id: str) -> Response:
        user = current_user(request)
        owned(user, subscription_id)
        subscription = await keelie.get_subscription(subscription_id)
        credentials = [
            await keelie.get_credential(link.id)
            for link in db.credentials(user.id, subscription_id)
        ]
        return page(
            request,
            "credentials.html",
            subscription=subscription,
            credentials=credentials,
            n=db.credential_count(subscription_id),
            token=await keelie.token_info(org_id, product_id),
        )

    @app.post("/subscriptions/{subscription_id}/credentials")
    async def create_credential(
        request: Request, subscription_id: str, csrf: str = Form(), n: int = Form()
    ) -> Response:
        check_csrf(request, csrf)
        user = current_user(request)
        owned(user, subscription_id)
        subscription = await keelie.get_subscription(subscription_id)
        if subscription.status != "active":
            return RedirectResponse(f"/subscriptions/{subscription_id}", status_code=303)
        if n < 0 or n > db.credential_count(subscription_id):
            raise NotYours
        # The nth credential of this subscription: a double click sends the same n.
        key = action_key(key_namespace, "credential", subscription_id, str(n))
        created, replayed = await keelie.create_credential(subscription_id, key)
        db.add_credential(created.id, user.id, subscription_id, created.client_id)
        log.info("credential %s for %s (replayed=%s)", created.id, subscription_id, replayed)
        response = page(
            request,
            "credential_created.html",
            subscription=subscription,
            created=created,
            # The secret goes into this one response and nowhere else.
            secret=created.credential.client_secret if created.credential else None,
            token=await keelie.token_info(org_id, product_id),
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/credentials/{credential_id}/revoke")
    async def revoke_credential(
        request: Request, credential_id: str, csrf: str = Form()
    ) -> Response:
        check_csrf(request, csrf)
        user = current_user(request)
        link = db.owned_credential(user.id, credential_id)
        if link is None:
            raise NotYours
        try:
            await keelie.revoke_credential(credential_id)
        except KeelieError as error:
            # Revoked already: the outcome the Consumer wanted.
            if error.code != "entity-conflict":
                raise
        return RedirectResponse(
            f"/subscriptions/{link.subscription_id}/credentials", status_code=303
        )

    return app
