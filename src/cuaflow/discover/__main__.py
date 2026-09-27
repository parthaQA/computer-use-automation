"""Same prompt records the first time and replays after that.

    python -m cuaflow.discover "open base_url, enter operator id and password, click on sign on button. search member id 12345 and click on search button" --surface web --tenant lab
    python -m cuaflow.discover "open app, enter operator id and password, click on sign on button. search member id 12345 and click on search button" --surface desktop --tenant lab
"""

from __future__ import annotations

import asyncio
import sys

from cuaflow.dispatch import EXAMPLE, FlowOrchestrator, parse_args, read_prompt
from cuaflow.settings import load_settings
from cuaflow.tenant import make_tenant


async def main(argv: list[str] | None = None) -> int:
    """CLI entry: discover (or catalog replay) a user goal."""
    args = parse_args(argv)
    prompt = read_prompt(args)
    if not prompt:
        print("a task prompt is required", file=sys.stderr)
        print(f'example: python -m cuaflow.discover "{EXAMPLE}"', file=sys.stderr)
        return 2
    if args.headless and not args.policy_only:
        print("Policy blocked this rewritten context: headless is not allowed during discover")
        print("Use a visible browser for discover, or replay with: python -m cuaflow.replay <capability> --headless")
        return 1
    try:
        tenant = make_tenant(args.tenant, args.tenant_url, path=load_settings().cua_tenants_path)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if tenant:
        print(f"tenant={tenant.id}  url={tenant.origin() or '(env MEMSYS_BASE_URL)'}  app={tenant.app_target()}")
    return await FlowOrchestrator().run(
        prompt,
        headed=not args.headless,
        policy_only=args.policy_only,
        surface=args.surface,
        tenant=tenant,
        rediscover=args.rediscover,
    )


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
