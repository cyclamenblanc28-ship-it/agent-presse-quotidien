import argparse
import asyncio

import mistralai.workflows as workflows

from worker.workflows.brief_quotidien import BriefInput, BriefQuotidien


async def run(post: bool) -> int:
    workflow_classes = workflows.discover_all_workflows_in_package("worker.workflows")
    worker_task = workflows.run_worker(workflow_classes, detach=True)
    try:
        await asyncio.sleep(3)
        result = await workflows.execute_workflow(
            BriefQuotidien,
            BriefInput(dry_run=not post),
        )
        print()
        print(result.message)
        if post and not result.posted_to_slack:
            print()
            print("Le brief n'a pas pu être publié sur Slack : vérifiez SLACK_BOT_TOKEN et SLACK_CHANNEL.")
            return 1
        return 0
    finally:
        worker_task.cancel()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Exécute une fois le brief quotidien vérifié (collecte, vérification croisée, affichage)."
    )
    parser.add_argument(
        "--post",
        action="store_true",
        help="Publie aussi le brief sur Slack (sinon, simple affichage).",
    )
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(run(args.post)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
