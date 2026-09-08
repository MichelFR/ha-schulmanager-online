#!/usr/bin/env python3
"""Interactive probe for the Schulmanager Online API.

Run it with no arguments and it asks how you want to log in, then offers a menu
of things to fetch. It drives the same client the Home Assistant integration
uses, so whatever works here works there. See ``docs/api.md`` for the protocol.

    python tools/smo_cli.py                     # interactive
    python tools/smo_cli.py --dump dumps/       # interactive, saving responses

Non-interactive subcommands remain available for scripting:

    python tools/smo_cli.py schools "Gymnasium Zeven"
    python tools/smo_cli.py login
    python tools/smo_cli.py call get-homework --module homework \
        --params '{"student":{"id":1234}}'

Credentials may be pre-seeded with ``SMO_EMAIL`` / ``SMO_PASSWORD`` /
``SMO_INSTITUTION_ID``; the interactive flow prompts for whatever is missing and
never echoes the password.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import logging
import os
import sys
import webbrowser
from datetime import date, timedelta
from getpass import getpass
from pathlib import Path
from types import ModuleType
from typing import Any

from aiohttp import ClientSession


def _load_api_module():
    """Import api.py without importing the Home Assistant package.

    The integration's ``__init__.py`` pulls in ``homeassistant``, which the probe
    does not need. But ``api.py`` uses relative imports, so it cannot be loaded
    as a lone file either — it needs a package to be relative *to*. Standing up
    a synthetic package rooted at the integration directory gives it one, and
    ``.const`` / ``.model`` then resolve normally.
    """
    directory = (
        Path(__file__).resolve().parent.parent
        / "custom_components"
        / "schulmanager_online"
    )
    package = ModuleType("smo_pkg")
    package.__path__ = [str(directory)]
    sys.modules["smo_pkg"] = package

    spec = importlib.util.spec_from_file_location("smo_pkg.api", directory / "api.py")
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {directory / 'api.py'}")
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve their own module out of sys.modules, so register it
    # before executing or every @dataclass in there blows up.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


api = _load_api_module()
SchulmanagerAuthError = api.SchulmanagerAuthError
SchulmanagerClient = api.SchulmanagerClient
SchulmanagerError = api.SchulmanagerError
SchulmanagerMultipleAccounts = api.SchulmanagerMultipleAccounts
SchulmanagerTwoFactorRequired = api.SchulmanagerTwoFactorRequired

_LOGGER = logging.getLogger("smo_cli")


# --------------------------------------------------------------------------- #
# output helpers
# --------------------------------------------------------------------------- #


def _emit(name: str, payload: Any, dump_dir: Path | None) -> None:
    """Print a payload and optionally persist it next to the others."""
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    print(text)
    if dump_dir is not None:
        dump_dir.mkdir(parents=True, exist_ok=True)
        target = dump_dir / f"{name}.json"
        target.write_text(text + "\n", encoding="utf-8")
        print(f"\n  → saved to {target}")


def _ask(prompt: str, default: str | None = None) -> str:
    """Read a line, offering a default."""
    suffix = f" [{default}]" if default else ""
    while True:
        answer = input(f"{prompt}{suffix}: ").strip()
        if answer:
            return answer
        if default is not None:
            return default


def _choose(prompt: str, options: list[str]) -> int:
    """Show a numbered menu and return the chosen index."""
    print(f"\n{prompt}")
    for index, option in enumerate(options, start=1):
        print(f"  {index}) {option}")
    while True:
        raw = input("> ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        print(f"Please enter a number between 1 and {len(options)}.")


# --------------------------------------------------------------------------- #
# interactive login
# --------------------------------------------------------------------------- #


async def _pick_school(client: SchulmanagerClient) -> dict[str, Any]:
    """Search the public directory and let the user pick a school."""
    while True:
        query = _ask("\nSchool name, city or postcode")
        schools = await client.async_find_schools(query)
        if not schools:
            print("No school matched. Try a shorter query.")
            continue

        labels = [
            f"{school['name']} — {school.get('zipcode', '')} "
            f"{school.get('city', '')} (id {school['id']})".strip()
            for school in schools
        ]
        labels.append("Search again")
        index = _choose("Which school?", labels)
        if index < len(schools):
            return schools[index]


async def _sso_login(client: SchulmanagerClient, method: Any) -> None:
    """Walk the user through a browser single sign-on flow.

    The credentials go to the school's identity provider, never through here, so
    the browser has to do that part. Asking for ``mobile-app=true`` makes the
    server end the flow on a URL carrying the user device, which is what turns a
    browser session into a token this client can use.
    """
    url = method.url
    print(f"\n{method.label}")
    print("\n  1. Open this URL and sign in:\n")
    print(f"     {url}\n")
    print("  2. When you land on a blank or error page, copy the full address")
    print("     from the browser's address bar — it ends in '#userdevice=...'.")

    if _ask("\nOpen it in your browser now?", default="y").lower().startswith("y"):
        webbrowser.open(url)

    while True:
        pasted = _ask("\nPaste the final URL")
        try:
            device = api.parse_user_device(pasted)
        except SchulmanagerAuthError as err:
            print(f"  ! {err}")
            continue
        await client.async_login_with_user_device(device, reason="smo-cli")
        print("\n  ✓ Signed in via single sign-on.")
        print("    Keep the user device below to log in again without the browser:")
        print(f"    {json.dumps(client.user_device)}")
        return


async def _interactive_login(
    client: SchulmanagerClient, args: argparse.Namespace
) -> None:
    """Ask how to log in, then do it, resolving 2FA and school ambiguity."""
    index = _choose(
        "How do you want to log in?",
        [
            "Email and password",
            "Select a school first (needed for single sign-on)",
        ],
    )

    if index == 1:
        school = await _pick_school(client)
        client.institution_id = school["id"]

        methods = await client.async_get_sso_methods(school["id"])
        if methods:
            labels = ["Email and password", *(method.label for method in methods)]
            choice = _choose(f"How do you want to sign in to {school['name']}?", labels)
            if choice > 0:
                await _sso_login(client, methods[choice - 1])
                return
        else:
            print(f"\n{school['name']} offers no single sign-on — using a password.")

    email = args.email or _ask("\nEmail address or username")
    password = args.password or getpass("Password (not echoed): ")
    client.set_credentials(email, password)

    two_factor_code: str | None = None
    while True:
        try:
            await client.async_login(two_factor_code)
        except SchulmanagerMultipleAccounts as err:
            print("\nThis account exists at several schools.")
            labels = [
                f"{account.get('institutionName') or account}"
                for account in err.accounts
            ]
            chosen = err.accounts[_choose("Which one?", labels)]
            client.institution_id = chosen.get("institutionId") or chosen.get("id")
            continue
        except SchulmanagerTwoFactorRequired as err:
            source = "your email" if err.method == "email" else "your authenticator app"
            two_factor_code = _ask(f"\nEnter the code from {source}")
            continue
        return


# --------------------------------------------------------------------------- #
# interactive menu
# --------------------------------------------------------------------------- #


def _students(user: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Collect the students the logged-in user may look at.

    A student account carries ``associatedStudent``; a parent account carries one
    ``associatedParents`` entry per child.
    """
    if not user:
        return []
    students = []
    if own := user.get("associatedStudent"):
        students.append(own)
    for parent in user.get("associatedParents") or []:
        if child := parent.get("student"):
            students.append(child)
    return [student for student in students if student.get("id")]


def _describe(student: dict[str, Any]) -> str:
    """Render a student for a menu line."""
    name = f"{student.get('firstname', '')} {student.get('lastname', '')}".strip()
    return f"{name or 'unnamed'} (id {student['id']})"


def _pick_student(user: dict[str, Any] | None) -> dict[str, Any] | None:
    """Choose a student, skipping the prompt when there is only one."""
    students = _students(user)
    if not students:
        print(
            "\nNo student is associated with this account — this looks like a "
            "teacher or admin login, which the student endpoints reject."
        )
        return None
    if len(students) == 1:
        return students[0]
    return students[_choose("Which student?", [_describe(s) for s in students])]


async def _menu(client: SchulmanagerClient, dump_dir: Path | None) -> None:
    """Offer the interesting endpoints until the user quits."""
    actions = [
        "User object from the login response",
        "login-status (verify the token)",
        "Homework",
        "Exams (today + 6 weeks)",
        "Timetable (this week)",
        "Letters",
        "Class hours (the period grid)",
        "Everything the Home Assistant integration polls",
        "Call an arbitrary endpoint",
        "Quit",
    ]

    while True:
        choice = _choose("What do you want to fetch?", actions)

        if choice == len(actions) - 1:
            return

        try:
            if choice == 0:
                _emit("login-user", client.user, dump_dir)
            elif choice == 1:
                _emit("login-status", await client.async_call("login-status"), dump_dir)
            elif choice == 2:
                if student := _pick_student(client.user):
                    _emit(
                        "get-homework",
                        await client.async_call(
                            "get-homework",
                            {"student": {"id": student["id"]}},
                            module="homework",
                        ),
                        dump_dir,
                    )
            elif choice == 3:
                if student := _pick_student(client.user):
                    today = date.today()
                    _emit(
                        "get-exams",
                        await client.async_call(
                            "get-exams",
                            {
                                "student": {"id": student["id"]},
                                "start": today.isoformat(),
                                "end": (today + timedelta(weeks=6)).isoformat(),
                            },
                            module="exams",
                        ),
                        dump_dir,
                    )
            elif choice == 4:
                if student := _pick_student(client.user):
                    today = date.today()
                    monday = today - timedelta(days=today.weekday())
                    _emit(
                        "get-actual-lessons",
                        await client.async_call(
                            "get-actual-lessons",
                            {
                                "student": {"id": student["id"]},
                                "start": monday.isoformat(),
                                "end": (monday + timedelta(days=6)).isoformat(),
                            },
                            module="schedules",
                        ),
                        dump_dir,
                    )
            elif choice == 5:
                _emit("get-letters", await client.async_call("get-letters"), dump_dir)
            elif choice == 6:
                _emit(
                    "get-class-hours",
                    await client.async_call("get-class-hours"),
                    dump_dir,
                )
            elif choice == 7:
                _emit("coordinator-payload", await client.async_get_data(), dump_dir)
            elif choice == 8:
                endpoint = _ask("\nEndpoint name (e.g. get-homework)")
                module = _ask("Module name (empty for none)", default="")
                raw = _ask("Parameters as JSON", default="{}")
                _emit(
                    endpoint,
                    await client.async_call(
                        endpoint, json.loads(raw), module=module or None
                    ),
                    dump_dir,
                )
        except SchulmanagerError as err:
            print(f"\n  ! {err}")
        except (ValueError, KeyError) as err:
            print(f"\n  ! bad input: {err}")


# --------------------------------------------------------------------------- #
# entry points
# --------------------------------------------------------------------------- #


def _parse_params(raw: str | None) -> dict[str, Any] | None:
    """Decode the --params JSON object."""
    if raw is None:
        return None
    params = json.loads(raw)
    if not isinstance(params, dict):
        raise SystemExit("--params must be a JSON object")
    return params


async def _run(args: argparse.Namespace) -> int:
    """Dispatch one subcommand, or run the interactive session."""
    dump_dir = args.dump_dir

    async with ClientSession() as session:
        client = SchulmanagerClient(
            session,
            args.email or "",
            args.password or "",
            institution_id=args.school,
        )

        if args.command is None:
            print("Schulmanager Online API probe")
            await _interactive_login(client, args)
            name = (client.user or {}).get("firstname") or "unknown user"
            print(f"\nLogged in as {name}.")
            await _menu(client, dump_dir)
            return 0

        if args.command == "schools":
            _emit("find-schools", await client.async_find_schools(args.query), dump_dir)
            return 0

        if not args.email or not args.password:
            raise SystemExit(
                "set SMO_EMAIL and SMO_PASSWORD (or --email/--password), "
                "or run without a subcommand for the interactive flow"
            )

        await client.async_login()
        _LOGGER.info("logged in, token length %d", len(client.token or ""))

        if args.command == "login":
            _emit("login-user", client.user, dump_dir)
        elif args.command == "whoami":
            _emit("login-status", await client.async_call("login-status"), dump_dir)
        elif args.command == "call":
            _emit(
                args.endpoint,
                await client.async_call(
                    args.endpoint, _parse_params(args.params), module=args.module
                ),
                dump_dir,
            )
        return 0


def _build_parser() -> argparse.ArgumentParser:
    """Describe the command line."""
    parser = argparse.ArgumentParser(
        prog="smo_cli",
        description="Probe the unofficial Schulmanager Online API. "
        "Run without a subcommand for an interactive session.",
    )
    parser.add_argument("--email", default=os.environ.get("SMO_EMAIL"))
    parser.add_argument("--password", default=os.environ.get("SMO_PASSWORD"))
    parser.add_argument(
        "--school",
        type=int,
        default=_env_int("SMO_INSTITUTION_ID"),
        help="institution id, as returned by the 'schools' command",
    )
    parser.add_argument("--dump", metavar="DIR", help="write responses to DIR")
    parser.add_argument("--trace", action="store_true", help="log request bodies")

    sub = parser.add_subparsers(dest="command")

    schools = sub.add_parser("schools", help="search the public school directory")
    schools.add_argument("query")

    sub.add_parser("login", help="log in and show the user object")
    sub.add_parser("whoami", help="log in and call login-status")

    call = sub.add_parser("call", help="invoke an arbitrary RPC endpoint")
    call.add_argument("endpoint")
    call.add_argument("--module", help="moduleName, e.g. homework or exams")
    call.add_argument("--params", help="parameters as a JSON object")

    return parser


def _env_int(name: str) -> int | None:
    """Read an optional integer from the environment."""
    raw = os.environ.get(name)
    return int(raw) if raw else None


def main() -> int:
    """Entry point."""
    args = _build_parser().parse_args()
    args.dump_dir = Path(args.dump).expanduser() if args.dump else None
    logging.basicConfig(
        level=logging.DEBUG if args.trace else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.trace:
        logging.getLogger("smo_api").setLevel(logging.DEBUG)

    try:
        return asyncio.run(_run(args))
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.")
        return 130
    except SchulmanagerMultipleAccounts as err:
        print("This email exists at several schools. Re-run with --school <id>:")
        print(json.dumps(err.accounts, indent=2, ensure_ascii=False))
        return 2
    except SchulmanagerTwoFactorRequired as err:
        print(f"Two-factor required ({err.method}); run interactively to enter a code.")
        return 3
    except SchulmanagerAuthError as err:
        print(f"Authentication failed: {err}")
        return 4
    except SchulmanagerError as err:
        print(f"API error: {err}")
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
