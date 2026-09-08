# The unofficial Schulmanager Online API

Notes taken while reverse engineering <https://login.schulmanager-online.de>.
Everything here was derived from the production Angular bundle served by that
host and confirmed against the live API with `tools/smo_cli.py`. There is no
official documentation and no stability guarantee.

Base URL: `https://login.schulmanager-online.de/api`

## Authentication

Three requests, all `POST` with a JSON body.

### 1. `POST /api/get-salt`

```json
{ "emailOrUsername": "me@example.com", "userId": null, "institutionId": null }
```

The response body is a bare JSON string: ~520 hex characters of per-account
salt. Unknown addresses get a salt too, so this cannot be used to enumerate
accounts. `institutionId` matters only when the same address exists at several
schools.

### 2. Derive the hash

PBKDF2-HMAC-SHA512, **99 999** iterations, **512-byte** output, hex encoded
(1024 characters). Two encoding details matter, both copied from the browser:

- The **password** is turned into bytes the way `charCodeAt` into a `Uint8Array`
  does, i.e. each UTF-16 code unit truncated to its low byte (`ord(c) & 0xFF`).
  For non-ASCII passwords this differs from UTF-8.
- The **salt** is the UTF-8 encoding of the hex string exactly as received (it is
  *not* decoded from hex first).

`hash_password()` in `api.py` reproduces this; it was verified byte-for-byte
against Node's WebCrypto running the site's own helper functions.

### 3. `POST /api/login`

```json
{
  "emailOrUsername": "me@example.com",
  "password": "plaintext",
  "hash": "<1024 hex chars>",
  "mobileApp": false,
  "userId": null,
  "twoFactorCode": null,
  "institutionId": null
}
```

The plaintext password is sent alongside the hash, and `hash` may be `null` —
the web app falls back to that when WebCrypto is unavailable. So the hash is not
a security boundary; TLS is.

Responses worth handling:

| Response | Meaning |
| --- | --- |
| `{ "jwt": "...", "user": {...}, "userDevice": {...} }` | Success |
| `{ "multipleAccounts": [...] }` | Address exists at several schools — retry with `institutionId` |
| `{ "requireTwoFactorEmailCode": true }` | Emailed second factor needed |
| `{ "requireTOTP": true }` | TOTP second factor needed |
| `401` | Wrong email or password |
| `403` + `{ "untrustedNetwork": true }` | School blocks logins from this network |
| `429` | Rate limited |

### Single sign-on

Many schools federate to an identity provider instead of holding passwords. The
login page decides which buttons to show by running four probes, each taking
`{ institutionId }` — **all of them work unauthenticated**, and a non-null answer
means that method is available:

| Probe endpoint | Enables | Example answer |
| --- | --- | --- |
| `get-sso-provider` | OIDC (IServ, Office 365) | `"iserv"` |
| `get-id-broker-kc-idp-hint` | Univention ID-Broker | `"stadtkoeln"` |
| `get-logodidact-school-id` | logoDIDACT | a school id |
| `get-vidis-idp-hint` | VIDIS | an IdP hint |

Confirmed live: institution 601 returns `"iserv"`, a municipal school returns
an ID-Broker hint such as `"stadtkoeln"`, and most schools return `null` for
all four.

The flows themselves are browser redirects, not API calls:

| Method | URL |
| --- | --- |
| OIDC | `/oidc/{institutionId}` |
| ID-Broker | `/id-broker?kc_idp_hint={hint}` |
| logoDIDACT | `/logodidact-sso/{schoolId}/-` |
| VIDIS | `/sso/vidis?vidis_idp_hint={hint}&institution_id={id}` |

`/id-broker` starts an ordinary Keycloak authorization-code + PKCE flow against
Univention's ID-Broker, keeping the `code_verifier` in an HttpOnly cookie scoped
to `/id-broker` and calling back to `/id-broker/callback`. The user's password
goes to the school's IdP, so this part genuinely needs a browser.

The way out for a non-browser client is the mobile-app variant. Adding
**`mobile-app=true`** makes the server finish by redirecting to a URL carrying
**`#userdevice=<url-encoded JSON>`** rather than dropping the browser into the
web app — the app watches its in-app browser for exactly that. Hand that object
to `POST /api/login-with-user-device` (below) and you have a token.

### Token handling

The JWT goes into `Authorization: Bearer <jwt>`. The server rolls it on nearly
every authenticated call and returns the replacement in the
**`x-new-bearer-token`** response header — clients must adopt it, which is how
the session slides forward without re-sending credentials. Sending
`X-Skip-Bearer-Token-Renewal: true` suppresses that.

`POST /api/login-status` with `{}` validates the current token and returns
`{ isAuthenticated, user, userDevice }`.

### Long-lived sessions

A successful login — password or single sign-on — also yields a `userDevice`.
`POST /api/login-with-user-device` with `{ "device": <that object>, "reason":
"..." }` mints a fresh JWT without the password, and the web app additionally
sends `X-Token: {"id":…,"key":…}` on every request. Storing the device instead
of the password is the right shape for Home Assistant, and for single sign-on
accounts it is the *only* shape — there is no password to store.

## Data: `POST /api/calls`

Everything after login is a batched RPC through one endpoint.

```json
{
  "bundleVersion": "50535480bb",
  "requests": [
    { "moduleName": "homework", "endpointName": "get-homework",
      "parameters": { "student": { "id": 1234 } } }
  ]
}
```

`bundleVersion` is the web app's build hash. It must be present and a non-empty
string, but the server does not validate the value — a bogus hash is accepted.

```json
{
  "results": [ { "status": 200, "data": [ ... ] } ],
  "systemStatusMessages": []
}
```

Note the **per-request status**: the HTTP response is `200` even when an
individual call fails. `400` carries `userError.germanErrorMessage`; `401` means
the token is dead. `moduleName` may be `null` for endpoints that do not belong
to a module.

The bundle exposes ~676 endpoint names. The ones relevant here:

| Module | Endpoint | Parameters |
| --- | --- | --- |
| — | `find-schools` | `{ query }` — **works unauthenticated** |
| — | `get-letters` | `{}` or `{ offset }` (pages of 15) |
| `homework` | `get-homework` | `{ student: { id } }` |
| `exams` | `get-exams` | `{ student: { id }, start, end }` |
| `schedules` | `get-actual-lessons` | `{ student: {...}, start, end }` (also `class`/`room`) |
| — | `get-class-hours` | `{}` — the period grid the timetable refers to |

Rate limits are announced per endpoint in `x-ratelimit-*`: 800 for `/api/calls`,
but only 5 for the HTML entry point, so do not poll the login page.

## Testing

`tools/smo_cli.py` drives the same client the integration uses. Run it with no
arguments and it asks how to log in — email and password, or search the public
school directory first — then offers a menu of endpoints:

```bash
python tools/smo_cli.py --dump dumps/
```

It resolves `multipleAccounts` and two-factor prompts interactively. Responses
are printed and, with `--dump DIR`, written to `DIR/<endpoint>.json`.

Non-interactive subcommands are there for scripting:

```bash
python tools/smo_cli.py schools "Gymnasium Zeven"     # no credentials needed
export SMO_EMAIL=me@example.com SMO_PASSWORD=secret
python tools/smo_cli.py call get-homework --module homework \
    --params '{"student":{"id":1234}}'
```

`--trace` logs request bodies and `--school ID` pins the institution.
