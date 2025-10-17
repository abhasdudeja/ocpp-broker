import importlib.resources
import textwrap

def read_wiki(section: str = "index") -> str:
    """
    Read packaged documentation from ocpp_broker.docs.<section>.md
    """
    try:
        # try package docs
        pkg = "ocpp_broker.docs"
        filename = f"{section}.md"
        with importlib.resources.open_text(pkg, filename) as f:
            return f.read()
    except Exception:
        # fallback to basic message
        return textwrap.dedent(f"## Documentation not found for section: {section}\n\nAvailable sections: index, installation, configuration, rest_api, architecture")
