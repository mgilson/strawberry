from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Tuple, Type

import click

from strawberry.cli.utils import load_schema
from strawberry.codegen import QueryCodegen, QueryCodegenPlugin
from strawberry.codegen.plugins.python import PythonPlugin
from strawberry.codegen.plugins.typescript import TypeScriptPlugin

if TYPE_CHECKING:
    from strawberry.codegen import CodegenResult


def _is_codegen_plugin(obj: object) -> bool:
    return (
        inspect.isclass(obj)
        and issubclass(obj, QueryCodegenPlugin)
        and obj is not QueryCodegenPlugin
    )


def _import_plugin(plugin: str) -> Optional[Type[QueryCodegenPlugin]]:
    module_name = plugin
    symbol_name: Optional[str] = None

    if ":" in plugin:
        module_name, symbol_name = plugin.split(":", 1)

    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:
        return None

    if symbol_name:
        obj = getattr(module, symbol_name)

        assert _is_codegen_plugin(obj)
        return obj
    else:
        symbols = {
            key: value
            for key, value in module.__dict__.items()
            if not key.startswith("__")
        }

        if "__all__" in module.__dict__:
            symbols = {
                name: symbol
                for name, symbol in symbols.items()
                if name in module.__dict__["__all__"]
            }

        for obj in symbols.values():
            if _is_codegen_plugin(obj):
                return obj

    return None


def _load_plugin(plugin_path: str) -> Type[QueryCodegenPlugin]:
    # try to import plugin_name from current folder
    # then try to import from strawberry.codegen.plugins

    plugin = _import_plugin(plugin_path)

    if plugin is None and "." not in plugin_path:
        plugin = _import_plugin(f"strawberry.codegen.plugins.{plugin_path}")

    if plugin is None:
        raise click.ClickException(f"Plugin {plugin_path} not found")

    return plugin


def _load_plugins(plugins: List[str]) -> List[Type[QueryCodegenPlugin]]:
    return [_load_plugin(plugin) for plugin in plugins]


class ConsolePlugin(QueryCodegenPlugin):

    def __init__(
        self, query: Path, output_dir: Path, plugins: List[QueryCodegenPlugin]
    ):
        self.query = query
        self.output_dir = output_dir
        self.plugins = plugins

    def on_start(self) -> None:
        click.echo(
            click.style(
                "The codegen is experimental. Please submit any bug at "
                "https://github.com/strawberry-graphql/strawberry\n",
                fg="yellow",
                bold=True,
            )
        )

        plugin_names = [plugin.__class__.__name__ for plugin in self.plugins]

        click.echo(
            click.style(
                f"Generating code for {self.query} using "
                f"{', '.join(plugin_names)} plugin(s)",
                fg="green",
            )
        )

    def on_end(self, result: CodegenResult) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        result.write(self.output_dir)

        click.echo(
            click.style(
                f"Generated {len(result.files)} files in {self.output_dir}", fg="green"
            )
        )


@click.command(short_help="Generate code from a query")
@click.option("--plugins", "-p", "selected_plugins", multiple=True, required=True)
@click.option("--cli-plugin", "cli_plugin", required=False)
@click.option(
    "--output-dir",
    "-o",
    default=".",
    help="Output directory",
    type=click.Path(path_type=Path, exists=False, dir_okay=True, file_okay=False),
)
@click.option("--schema", type=str, required=True)
@click.argument("query", type=click.Path(path_type=Path, exists=True), nargs=-1)
@click.option(
    "--app-dir",
    default=".",
    type=str,
    show_default=True,
    help=(
        "Look for the module in the specified directory, by adding this to the "
        "PYTHONPATH. Defaults to the current working directory. "
        "Works the same as `--app-dir` in uvicorn."
    ),
)
def codegen(
    schema: str,
    query: Tuple[Path, ...],
    app_dir: str,
    output_dir: Path,
    selected_plugins: List[str],
    cli_plugin: Optional[str] = None,
) -> None:
    schema_symbol = load_schema(schema, app_dir)

    console_plugin_type = _load_plugin(cli_plugin) if cli_plugin else ConsolePlugin

    plugin_types = _load_plugins(selected_plugins)

    for q in query:
        plugins = [plugin_type() for plugin_type in plugin_types]
        # Set the `query` parameter on the plugins.
        # This happens after `__init__` to maintain backward compatibility with
        # existing consumer plugins that may have custom logic in `__init__`
        # already.  In principle, we may change the signature of the plugins
        # in some future MAJOR release.
        for p in plugins:
            p.query = q
            # Adjust the names of the output files for the buildin plugins if there are multiple
            # files being generated.
            if len(query) > 1:
                if isinstance(p, PythonPlugin):
                    p.outfile_name = q.stem + ".py"
                elif isinstance(p, TypeScriptPlugin):
                    p.outfile_name = q.stem + ".js"

        console_plugin = console_plugin_type(q, output_dir, plugins)
        plugins.append(console_plugin)

        code_generator = QueryCodegen(schema_symbol, plugins=plugins)
        code_generator.run(q.read_text())
