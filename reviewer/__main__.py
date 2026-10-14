"""CLI entry point: python -m reviewer --repo owner/name --pr 123 [--dry-run]"""

import argparse
import json
import logging
import sys

from .config import Config, ConfigError
from .github_client import GitHubError
from .llm import LLMError
from .reviewer import review_pull_request
from .state import DEFAULT_STATE_PATH


def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="python -m reviewer",
        description="Review a GitHub pull request diff with an LLM.",
    )
    parser.add_argument("--repo", required=True, help="Repository as owner/name, e.g. octocat/hello-world")
    parser.add_argument("--pr", required=True, type=int, help="Pull request number")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the review instead of posting it on the PR",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="With --dry-run, print findings as JSON instead of readable text",
    )
    parser.add_argument(
        "--max-chunk-tokens",
        type=int,
        default=None,
        help="Override MAX_CHUNK_TOKENS from the environment",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to a .pr-reviewer.yaml config file (auto-loaded from the repo root when present)",
    )
    parser.add_argument(
        "--state-path",
        default=DEFAULT_STATE_PATH,
        help=f"Path to the incremental-review state file (default: {DEFAULT_STATE_PATH})",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Force a full re-review, ignoring recorded incremental state",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    return parser


def print_dry_run(result, as_json):
    if as_json:
        print(json.dumps(result["findings"], indent=2))
        return
    print(result["summary"])
    print()
    for comment in result["comments"]:
        print(f"--- {comment['path']}:{comment['line']} ---")
        print(comment["body"])
        print()
    if result["skipped_chunks"]:
        print(f"(skipped {result['skipped_chunks']} unparseable chunk(s))")


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        config = Config.from_env_and_file(args.config)
    except ConfigError as exc:
        parser.error(str(exc))
    if args.max_chunk_tokens is not None:
        config.max_chunk_tokens = args.max_chunk_tokens

    try:
        result = review_pull_request(
            config,
            args.repo,
            args.pr,
            dry_run=args.dry_run,
            state_path=args.state_path,
            full=args.full,
        )
    except (GitHubError, LLMError, ConfigError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print_dry_run(result, args.json)
    else:
        print(f"Posted review on {args.repo}#{args.pr}: {result['summary']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
