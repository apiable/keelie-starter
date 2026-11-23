# keelie-starter

**A Consumer website for an API that sells through [Keelie](https://apiable.io).** Clone it, set
your brand, and your customers can sign up, choose a plan, pay, and get a credential for your
API. You own the code.

It runs in two modes:

- **Demo mode.** No Keelie account and no Stripe account. An in-memory stand-in plays Keelie, and a
  button plays Stripe. Use it to see every page in five minutes.
- **Live mode.** Calls Keelie with your Provider admin credential. Payments go through Stripe.js
  into your own Stripe account.

> **Status: early.** Keelie is in development, and so is this starter. It is tested against
> Keelie's API version `2026-10-08`.

## See it in five minutes

You need [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/apiable/keelie-starter
cd keelie-starter
uv run keelie-starter
```

Open <http://127.0.0.1:8000>. Sign up, choose a plan, pay with the demo button, and create a
credential. Each of the three demo plans shows one way to sell:

| Plan | What it shows |
|---|---|
| **Free** | No payment. The Consumer gets a credential at once. |
| **Pro** | A monthly fee. The Consumer agrees to the terms and pays, then the plan turns `active`. |
| **Pay as you go** | No fee. The Consumer buys packs of credits. |

## What a Consumer can do

1. **Sign up**, with an email and a password. This site keeps the login. Keelie never sees it.
2. **Choose a plan** from the published plans of your product.
3. **Pay** the monthly fee, or **buy a pack** of credits, through Stripe.js.
4. **Get a credential**: a client ID and a client secret, shown once. **Revoke** it.
5. **See how to call your API** with it: the token address, the audience and a `curl` example.

## Go live

1. Sign up as a Provider and register your API's address. Follow steps 1 and 2 of the
   [Keelie SDK quickstart](https://github.com/apiable/keelie-sdk/blob/main/docs/quickstart.md).
2. Register your product from its OpenAPI file, then make and publish its plans.
3. Copy `.env.example` to `.env`. Set `KEELIE_MODE=live`, your admin credential, your org id and
   your product id.
4. Set your brand: `SITE_NAME`, `SITE_TAGLINE`, `ACCENT_COLOR`, `SCOPE_WORDS`.
5. Replace `templates/terms.html` with your terms, and set `TERMS_VERSION`.
6. Run it behind https: `uv run uvicorn keelie_starter.asgi:app --host 0.0.0.0 --port 8000`.

The site fails at startup if a live setting is missing, and names it.

## The rules this code keeps

They come from Keelie's consumer-site guide. Keep them when you change the code.

- **Every call to Keelie goes from the server.** The browser never calls Keelie. It never sees your
  admin secret or a Keelie token. Stripe.js gets three values: the publishable key, the account,
  and the client secret of one payment.
- **Every id from the browser is checked** against the logged-in user before Keelie is called. An
  id that is not theirs answers `404`.
- **Every write carries an Idempotency-Key made from the action**, so a double click on "Pay" or
  "Create credential" does the work once.
- **A payment is done when Keelie says so**, after Stripe's webhook. The site never marks a payment
  as paid.
- **Money is an integer of minor units. Credits are decimal strings.** Neither is ever a float.
- **No secret reaches the log**: not a client secret, a token or a password. The log holds ids and
  Keelie's `request_id`.
- **Errors are RFC 9457 problems.** The code branches on the problem type, shows `detail`, and
  logs `request_id`.

## Make it yours

| To change | Edit |
|---|---|
| Colours and type | `src/keelie_starter/static/site.css`, or `ACCENT_COLOR` |
| Pages and words | `src/keelie_starter/templates/` |
| The login | `src/keelie_starter/app.py` (signup, login, logout) and `db.py`. Swap in your own login: keep one Keelie subject for each of your users. |
| The database | `src/keelie_starter/db.py`. SQLite keeps the starter to one file. |
| A call to Keelie | `src/keelie_starter/keelie.py`. The contract is the reference for every field. |

## Layout

| Path | What it holds |
|---|---|
| `src/keelie_starter/app.py` | The pages. |
| `src/keelie_starter/keelie.py` | The only module that calls Keelie. |
| `src/keelie_starter/demo.py` | The in-memory stand-in for Keelie, for demo mode and the tests. |
| `src/keelie_starter/db.py` | The site's own records: users, sessions, and the ids Keelie gave. |
| `tests/` | The three purchase paths end to end, and the client against a fake transport. No network. |
| [`example-api/`](example-api/) | **Daylight**, the demo's API: sunrise and sunset, protected by the Keelie SDK. |

## Check it

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
```

## What it is not

- **Not in the path of your API calls.** Your Consumers call your API directly. The SDK in your
  API checks their tokens locally. This site only sells the access.
- **Not a sign-in for agents.** An agent can use a credential that a person pastes into it. For
  agents that connect by themselves through your sign-in, see prompt 06 of Keelie's consumer-site
  guide.
- **Not the balance page, spend cap or automatic top-up.** The Keelie API has them. Add them when
  you need them.

## Security

Report a vulnerability privately. See [SECURITY.md](SECURITY.md).

## Licence

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Copyright 2026 Apiable Oy.
