# cli/audit.py
import asyncio
import json

import typer
from rich.console import Console

from core.session import run_audit, run_qa
from core.chains import CHAINS, resolve_chain
from report.renderer import (
    render_audit, render_spinner_start,
    render_stream_token, render_error,
)

console = Console()
app     = typer.Typer()


def audit_command(
    address: str,
    chain:   str,
    config:  dict,
    model_override: str | None = None,
    no_stream: bool = False,
    qa_mode: bool = True,
    use_consensus: bool = True,
    debug: bool = False,
):
    """Core audit flow — callable from main CLI."""
    if model_override:
        config["openrouter"]["model"] = model_override

    render_spinner_start(
        address, chain, config["openrouter"]["model"]
    )

    async def _run():
        try:
            contract, result = await run_audit(
                address, chain, config,
                stream=not no_stream,
                use_consensus=use_consensus,
                debug=debug,
            )
        except ValueError as e:
            render_error(str(e))
            raise typer.Exit(1)
        except Exception as e:
            render_error(f"Fetch failed: {e}")
            raise typer.Exit(1)

        if no_stream:
            render_audit(result, address, chain)
            report = result
        else:
            raw_json = ""
            async for token in result:
                render_stream_token(token)
                raw_json += token
            console.print()
            try:
                report = json.loads(raw_json)
                report["slither_findings"] = contract.get("slither_findings", [])
            except json.JSONDecodeError:
                render_error("AI returned malformed JSON — try again.")
                raise typer.Exit(1)
            render_audit(report, address, chain)

        # Interactive Q&A loop
        if qa_mode:
            history = []
            audit_result = report

            while True:
                try:
                    question = console.input(
                        "\n[bold cyan]💬 Ask a follow-up[/bold cyan] "
                        "[dim](or press Enter to exit)[/dim]: "
                    ).strip()
                except (KeyboardInterrupt, EOFError):
                    break
                if not question:
                    break

                qa_gen = await run_qa(
                    address, chain, config, history, question,
                    audit_result=audit_result
                )
                console.print("\n[cyan]AI:[/cyan] ", end="")
                answer = ""
                async for tok in qa_gen:
                    render_stream_token(tok)
                    answer += tok
                console.print()

                history.append({"role": "user",      "content": question})
                history.append({"role": "assistant", "content": answer})

    asyncio.run(_run())
